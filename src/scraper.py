import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright

from . import parser
from .cookies import load as load_cookies


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sleep(cfg: dict) -> None:
    time.sleep(random.uniform(cfg["min_delay_seconds"], cfg["max_delay_seconds"]))


def _upsert_forum(conn: sqlite3.Connection, forum: dict) -> None:
    conn.execute(
        "INSERT INTO forums(kw, name, game, topic, first_seen) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(kw) DO UPDATE SET name=excluded.name, topic=excluded.topic",
        (forum["kw"], forum["name"], forum["game"], forum.get("topic", "leak"), _now()),
    )


def _upsert_thread(conn: sqlite3.Connection, forum_kw: str, t: dict) -> bool:
    """Returns True if it's a new thread."""
    now = _now()
    cur = conn.execute("SELECT tid FROM threads WHERE tid=?", (t["tid"],))
    existing = cur.fetchone()
    if existing:
        conn.execute(
            "UPDATE threads SET last_seen=?, title=COALESCE(?, title), "
            "reply_count=COALESCE(?, reply_count), deleted_at=NULL WHERE tid=?",
            (now, t.get("title"), t.get("reply_count"), t["tid"]),
        )
        return False
    conn.execute(
        "INSERT INTO threads(tid, forum_kw, title, author_name, author_id, "
        "reply_count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (t["tid"], forum_kw, t.get("title"), t.get("author_name"), t.get("author_id"),
         t.get("reply_count"), now, now),
    )
    return True


def _save_snapshot(conn: sqlite3.Connection, tid: int, page: int, html: str) -> None:
    conn.execute(
        "INSERT INTO snapshots(tid, page, captured_at, raw_html) VALUES (?, ?, ?, ?)",
        (tid, page, _now(), html),
    )


def _insert_posts(conn: sqlite3.Connection, tid: int, posts: list[dict]) -> int:
    n = 0
    for p in posts:
        if not p.get("floor"):
            continue
        try:
            conn.execute(
                "INSERT INTO posts(tid, floor, author_name, author_id, content, "
                "content_hash, posted_at, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, p["floor"], p.get("author_name"), p.get("author_id"),
                 p.get("content"), p["content_hash"], p.get("posted_at"), _now()),
            )
            n += 1
        except sqlite3.IntegrityError:
            pass  # already have this (tid, floor, content_hash)
    return n


def _crawl_forum(ctx: BrowserContext, conn: sqlite3.Connection, forum: dict, cfg: dict) -> dict:
    page = ctx.new_page()
    log = {"threads_seen": 0, "threads_new": 0, "posts_new": 0, "error": None}
    try:
        _upsert_forum(conn, forum)
        thread_tids: list[int] = []

        # 1. 列表页（贴吧用虚拟列表，需滚动触发渲染）
        for i in range(cfg["list_pages_per_round"]):
            url = f"https://tieba.baidu.com/f?kw={forum['kw']}&pn={i * 50}"
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # 滚动到底部触发虚拟列表渲染更多条目
            for _ in range(cfg.get("scroll_rounds", 5)):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(800)
            html = page.content()
            items = parser.parse_list_page(html)
            log["threads_seen"] += len(items)
            for t in items:
                is_new = _upsert_thread(conn, forum["kw"], t)
                if is_new:
                    log["threads_new"] += 1
                thread_tids.append(t["tid"])
            conn.commit()

        # 2. 详情页（仅新发现 + 最近一轮没抓过详情的，简化为：所有列表里看到的）
        for tid in thread_tids:
            for pn in range(1, cfg["thread_pages_max"] + 1):
                url = f"https://tieba.baidu.com/p/{tid}?pn={pn}"
                try:
                    page.goto(url, wait_until="networkidle", timeout=30_000)
                except Exception as e:
                    log["error"] = f"thread {tid} page {pn}: {e}"
                    break
                # 滚动触发虚拟列表加载更多楼层
                for _ in range(cfg.get("scroll_rounds", 5)):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(600)
                _sleep(cfg)
                html = page.content()
                # 帖子被删时，贴吧会跳到"出错啦"页；这种情况快照仍然存（留证），但停止翻页。
                if "出错啦" in html or "该帖已被删除" in html:
                    _save_snapshot(conn, tid, pn, html)
                    conn.execute(
                        "UPDATE threads SET deleted_at=? WHERE tid=? AND deleted_at IS NULL",
                        (_now(), tid),
                    )
                    conn.commit()
                    break
                _save_snapshot(conn, tid, pn, html)
                posts = parser.parse_thread_page(html)
                log["posts_new"] += _insert_posts(conn, tid, posts)
                conn.commit()
                # 楼层不足一页，说明已经到末页
                if len(posts) < 20:
                    break
    except Exception as e:
        log["error"] = str(e)
    finally:
        page.close()
    return log


def _detect_deletions(ctx: BrowserContext, conn: sqlite3.Connection, cfg: dict) -> int:
    """对最近 N 小时内见过、但本轮没再被列表页见到的帖子，逐个探活。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cfg["recheck_window_hours"])).isoformat()
    rows = conn.execute(
        "SELECT tid FROM threads WHERE deleted_at IS NULL AND last_seen < ? AND first_seen > ?",
        (cutoff, cutoff),
    ).fetchall()
    if not rows:
        return 0
    page = ctx.new_page()
    deleted = 0
    try:
        for row in rows:
            tid = row["tid"]
            try:
                page.goto(f"https://tieba.baidu.com/p/{tid}", wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                continue
            _sleep(cfg)
            html = page.content()
            if "出错啦" in html or "该帖已被删除" in html:
                _save_snapshot(conn, tid, 0, html)
                conn.execute("UPDATE threads SET deleted_at=? WHERE tid=?", (_now(), tid))
                deleted += 1
                conn.commit()
    finally:
        page.close()
    return deleted


def run_once(forums: list[dict], cfg: dict, db_path: Path, cookie_path: Path) -> None:
    from .db import connect
    conn = connect(db_path)
    cookies = load_cookies(cookie_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )
        ctx.add_cookies(cookies)
        try:
            for forum in forums:
                if not forum.get("enabled", True):
                    continue
                started = _now()
                cur = conn.execute(
                    "INSERT INTO crawl_log(started_at, forum_kw) VALUES (?, ?)",
                    (started, forum["kw"]),
                )
                log_id = cur.lastrowid
                conn.commit()
                log = _crawl_forum(ctx, conn, forum, cfg)
                conn.execute(
                    "UPDATE crawl_log SET ended_at=?, threads_seen=?, threads_new=?, "
                    "posts_new=?, error=? WHERE id=?",
                    (_now(), log["threads_seen"], log["threads_new"],
                     log["posts_new"], log["error"], log_id),
                )
                conn.commit()
                print(f"[{forum['name']}] seen={log['threads_seen']} new={log['threads_new']} "
                      f"posts+={log['posts_new']} err={log['error']}")
            n_del = _detect_deletions(ctx, conn, cfg)
            if n_del:
                print(f"[deletions] marked {n_del} thread(s) as deleted")
        finally:
            ctx.close()
            browser.close()
            conn.close()
