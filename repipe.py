"""重建 posts 后，重跑关键词预筛 + LLM 判定（不抓取）。"""
import sys; sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from src.db import connect
from src import filter as kw
from src import llm

db = Path("data/radar.db")
conn = connect(db)
n = kw.run(conn)
print(f"[filter] signal={n}", flush=True)
m = llm.run(conn, db)
print(f"[llm] judged={m}", flush=True)
conn.close()
