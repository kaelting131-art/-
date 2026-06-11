"""游戏雷达 Web 仪表盘。本地只读看板，展示已判定的爆料/强度情报。
启动：python -m src.web   →  http://127.0.0.1:8787
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, render_template_string, request

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "radar.db"

app = Flask(__name__)

TZ_CN = timezone(timedelta(hours=8))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(TZ_CN).strftime("%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        tags = json.loads(raw)
        return tags if isinstance(tags, list) else []
    except json.JSONDecodeError:
        return []


def _rows_to_items(rows) -> list[dict]:
    items = []
    for r in rows:
        d = dict(r)
        d["tags"] = _parse_tags(d.get("llm_tags"))
        d["time"] = _fmt_time(d.get("first_seen"))
        d["deleted"] = bool(d.get("deleted_at"))
        items.append(d)
    return items


TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>游戏雷达</title>
<style>
  :root {
    --bg: #0f1117; --panel: #171a23; --border: #262b38;
    --text: #d7dce5; --dim: #8b93a3; --accent: #e8a33d;
    --leak: #e06c75; --strength: #61afef; --ok: #98c379;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         padding: 24px; max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  h1 .sub { color: var(--dim); font-size: 13px; font-weight: normal; margin-left: 8px; }
  h2 { font-size: 15px; margin: 28px 0 10px; color: var(--accent); }
  .chips { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 4px; }
  .chip { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
          padding: 8px 14px; text-align: center; min-width: 90px; }
  .chip b { display: block; font-size: 18px; color: var(--accent); }
  .chip span { font-size: 12px; color: var(--dim); }
  .filters { margin: 10px 0; font-size: 13px; }
  .filters a { color: var(--dim); text-decoration: none; margin-right: 12px;
               padding: 2px 10px; border-radius: 6px; border: 1px solid transparent; }
  .filters a.on { color: var(--accent); border-color: var(--accent); }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
          padding: 12px 16px; margin-bottom: 10px; }
  .card .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .card a.title { color: var(--text); font-weight: 600; text-decoration: none; }
  .card a.title:hover { color: var(--accent); }
  .badge { font-size: 11px; border-radius: 5px; padding: 1px 7px; white-space: nowrap; }
  .badge.forum { background: #20253244; border: 1px solid var(--border); color: var(--dim); }
  .badge.conf { border: 1px solid var(--ok); color: var(--ok); }
  .badge.del { background: var(--leak); color: #fff; }
  .badge.tag { border: 1px solid var(--strength); color: var(--strength); }
  .summary { color: var(--dim); margin-top: 4px; }
  .meta { color: var(--dim); font-size: 12px; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--dim); font-weight: normal; }
  td.err { color: var(--leak); max-width: 320px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--dim); padding: 16px; text-align: center; }
  footer { color: var(--dim); font-size: 12px; margin-top: 28px; text-align: center; }
</style>
</head>
<body>
<h1>🎮 游戏雷达 <span class="sub">贴吧内鬼爆料 · 强度分析 · 每 5 分钟自动刷新</span></h1>

<div class="chips">
  <div class="chip"><b>{{ stats.threads }}</b><span>帖子</span></div>
  <div class="chip"><b>{{ stats.posts }}</b><span>楼层</span></div>
  <div class="chip"><b>{{ stats.signals }}</b><span>预筛命中</span></div>
  <div class="chip"><b>{{ stats.leaks }}</b><span>确认爆料</span></div>
  <div class="chip"><b>{{ stats.strength }}</b><span>强度结论</span></div>
  <div class="chip"><b>{{ stats.deleted }}</b><span>已删帖</span></div>
  <div class="chip"><b>{{ stats.last_crawl }}</b><span>最近抓取</span></div>
</div>

<div class="filters">
  {% for g in games %}
  <a href="?game={{ g }}" class="{{ 'on' if g == game else '' }}">{{ '全部' if g == 'all' else g }}</a>
  {% endfor %}
</div>

<h2>🕵️ 爆料情报（置信度排序）</h2>
{% for it in leaks %}
<div class="card">
  <div class="head">
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">{{ it.title or '(无标题)' }}</a>
    <span class="badge conf">conf {{ it.llm_confidence }}</span>
    {% if it.deleted %}<span class="badge del">已删 ⚠️</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}</div>
</div>
{% else %}
<div class="empty">暂无确认爆料</div>
{% endfor %}

<h2>⚔️ 强度结论（置信度排序）</h2>
{% for it in strengths %}
<div class="card">
  <div class="head">
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">{{ it.title or '(无标题)' }}</a>
    <span class="badge conf">conf {{ it.llm_confidence }}</span>
    {% if it.deleted %}<span class="badge del">已删 ⚠️</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}</div>
</div>
{% else %}
<div class="empty">暂无强度结论</div>
{% endfor %}

<h2>🗑️ 删帖监控（被删的帖往往说明爆料是真的）</h2>
{% for it in deleted %}
<div class="card">
  <div class="head">
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">{{ it.title or '(无标题)' }}</a>
    <span class="badge del">删于 {{ it.del_time }}</span>
  </div>
  {% if it.llm_summary %}<div class="summary">{{ it.llm_summary }}</div>{% endif %}
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}（快照已留存）</div>
</div>
{% else %}
<div class="empty">暂无删帖记录</div>
{% endfor %}

<h2>📋 抓取日志</h2>
<table>
  <tr><th>时间</th><th>吧</th><th>看到</th><th>新帖</th><th>新楼层</th><th>错误</th></tr>
  {% for r in logs %}
  <tr>
    <td>{{ r.time }}</td><td>{{ r.forum_kw or '-' }}</td>
    <td>{{ r.threads_seen }}</td><td>{{ r.threads_new }}</td><td>{{ r.posts_new }}</td>
    <td class="err">{{ r.error or '' }}</td>
  </tr>
  {% endfor %}
</table>

<footer>游戏雷达 · 数据来源：百度贴吧 · LLM 判定：龙虾 (DeepSeek V4)</footer>
</body>
</html>"""


@app.route("/")
def index():
    game = request.args.get("game", "all")
    conn = _conn()
    try:
        games = ["all"] + [r[0] for r in conn.execute(
            "SELECT DISTINCT game FROM forums ORDER BY game").fetchall()]
        game_where = "" if game == "all" else "AND f.game = :game"
        params = {"game": game}

        leaks = _rows_to_items(conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen, t.deleted_at,
                   t.llm_confidence, t.llm_summary, t.llm_tags
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE f.topic = 'leak' AND t.llm_is_leak = 1 {game_where}
            ORDER BY t.llm_confidence DESC, t.first_seen DESC LIMIT 30
        """, params).fetchall())

        strengths = _rows_to_items(conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen, t.deleted_at,
                   t.llm_confidence, t.llm_summary, t.llm_tags
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE f.topic = 'strength' AND t.llm_is_leak = 1 {game_where}
            ORDER BY t.llm_confidence DESC, t.first_seen DESC LIMIT 30
        """, params).fetchall())

        deleted_rows = conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen,
                   t.deleted_at, t.llm_summary, t.llm_tags
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE t.deleted_at IS NOT NULL {game_where}
            ORDER BY t.deleted_at DESC LIMIT 20
        """, params).fetchall()
        deleted = _rows_to_items(deleted_rows)
        for d in deleted:
            d["del_time"] = _fmt_time(d.get("deleted_at"))

        logs = [
            {**dict(r), "time": _fmt_time(r["started_at"])}
            for r in conn.execute(
                "SELECT started_at, forum_kw, threads_seen, threads_new, posts_new, error "
                "FROM crawl_log ORDER BY id DESC LIMIT 15").fetchall()
        ]

        stats = {
            "threads": conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
            "posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM posts WHERE is_signal=1").fetchone()[0],
            "leaks": conn.execute(
                "SELECT COUNT(*) FROM threads t JOIN forums f ON f.kw=t.forum_kw "
                "WHERE f.topic='leak' AND t.llm_is_leak=1").fetchone()[0],
            "strength": conn.execute(
                "SELECT COUNT(*) FROM threads t JOIN forums f ON f.kw=t.forum_kw "
                "WHERE f.topic='strength' AND t.llm_is_leak=1").fetchone()[0],
            "deleted": conn.execute(
                "SELECT COUNT(*) FROM threads WHERE deleted_at IS NOT NULL").fetchone()[0],
            "last_crawl": _fmt_time(conn.execute(
                "SELECT MAX(ended_at) FROM crawl_log").fetchone()[0]),
        }
    finally:
        conn.close()

    return render_template_string(
        TEMPLATE, stats=stats, leaks=leaks, strengths=strengths,
        deleted=deleted, logs=logs, games=games, game=game)


def main() -> None:
    app.run(host="127.0.0.1", port=8787, debug=False)


if __name__ == "__main__":
    main()
