"""从所有详情页快照用新 parser 重建 posts 表。旧 posts 数据脏，全部重来。
快照存档的价值正在于此：解析逻辑迭代不用重新爬。"""
import sys; sys.stdout.reconfigure(encoding="utf-8")
import sqlite3
from pathlib import Path

from src.db import connect, snapshot_html
from src import parser

conn = connect(Path("data/radar.db"))
n_before = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

conn.execute("DELETE FROM posts")
conn.execute("""UPDATE threads SET llm_judged=0, llm_is_leak=NULL, llm_is_bait=NULL,
    llm_confidence=NULL, llm_summary=NULL, llm_tags=NULL, llm_attempts=0""")
conn.commit()

snaps = conn.execute("SELECT * FROM snapshots WHERE page>=1 ORDER BY captured_at").fetchall()
ins = 0
for s in snaps:
    html = snapshot_html(s)
    for p in parser.parse_thread_page(html):
        if not p.get("floor"):
            continue
        try:
            conn.execute(
                "INSERT INTO posts(tid,floor,author_name,author_id,content,content_hash,"
                "posted_at,first_seen,is_op) VALUES (?,?,?,?,?,?,?,?,?)",
                (s["tid"], p["floor"], p.get("author_name"), p.get("author_id"),
                 p.get("content"), p["content_hash"], p.get("posted_at"),
                 s["captured_at"], p.get("is_op", 0)),
            )
            ins += 1
        except sqlite3.IntegrityError:
            pass
conn.commit()

n_after = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
op = conn.execute("SELECT COUNT(*) FROM posts WHERE is_op=1").fetchone()[0]
f1 = conn.execute("SELECT COUNT(*) FROM posts WHERE floor=1").fetchone()[0]
threads_with_op = conn.execute(
    "SELECT COUNT(DISTINCT tid) FROM posts WHERE is_op=1").fetchone()[0]
threads_total = conn.execute("SELECT COUNT(DISTINCT tid) FROM posts").fetchone()[0]
print(f"快照 {len(snaps)} 份 -> posts {n_before} 重建为 {n_after}")
print(f"首楼(floor=1) {f1}，楼主楼层 {op}，有楼主的帖 {threads_with_op}/{threads_total}")
