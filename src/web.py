"""游戏雷达 Web 仪表盘。本地只读看板，展示已判定的爆料/强度情报。
启动：python -m src.web   →  http://127.0.0.1:8787

视觉方向「相控阵雷达终端」：磷光绿 HUD + 等宽数字 + 雷达扫描动画。
图表全部是 Python 侧生成的内联 SVG，零前端依赖、离线可用。
"""
import json
import sqlite3
import subprocess
import sys
import threading
from collections import Counter
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_read_col() -> None:
    """老库补 read_at 列（已读标记）。"""
    conn = _conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        if "read_at" not in cols:
            conn.execute("ALTER TABLE threads ADD COLUMN read_at TEXT")
            conn.commit()
    finally:
        conn.close()


_ensure_read_col()

# 「立即抓取」按钮触发的管线子进程（同一时间只允许一个在跑）
_crawl_lock = threading.Lock()
_crawl_proc: subprocess.Popen | None = None


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


def _conf_cls(v) -> str:
    if not v:
        return "lo"
    return "hi" if v >= 8 else ("mid" if v >= 5 else "lo")


def _rows_to_items(rows) -> list[dict]:
    items = []
    for r in rows:
        d = dict(r)
        d["tags"] = _parse_tags(d.get("llm_tags"))
        d["time"] = _fmt_time(d.get("first_seen"))
        d["deleted"] = bool(d.get("deleted_at"))
        d["read"] = bool(d.get("read_at"))
        d["conf"] = d.get("llm_confidence") or 0
        d["conf_cls"] = _conf_cls(d.get("llm_confidence"))
        items.append(d)
    return items


# ─────────────────────────── 样式 ───────────────────────────

BASE_CSS = """
  :root {
    --bg0: #06090b; --bg1: #0b1014; --bg2: #10171c;
    --line: #1c2830; --line-hi: #2a3c46;
    --ink: #cfe3dc; --dim: #6d8089; --faint: #45565e;
    --radar: #2ee59d; --radar-dim: #1a8f64; --radar-glow: rgba(46,229,157,.16);
    --amber: #ffb648; --amber-glow: rgba(255,182,72,.14);
    --ice: #5cc8ff; --ice-glow: rgba(92,200,255,.13);
    --red: #ff5664; --red-glow: rgba(255,86,100,.15);
    --mono: "Cascadia Mono", Consolas, "Courier New", monospace;
    --disp: "Rajdhani", "Cascadia Mono", "Microsoft YaHei", sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scrollbar-color: var(--line-hi) var(--bg0); }
  body {
    background: var(--bg0); color: var(--ink);
    font: 14px/1.65 "Microsoft YaHei", "PingFang SC", sans-serif;
    padding: 26px 28px 60px; max-width: 1280px; margin: 0 auto;
    position: relative;
  }
  /* 网格底纹 + 顶部辉光 */
  body::before {
    content: ""; position: fixed; inset: 0; z-index: -2; pointer-events: none;
    background:
      radial-gradient(ellipse 70% 38% at 50% -6%, rgba(46,229,157,.07), transparent),
      linear-gradient(90deg, rgba(46,229,157,.025) 1px, transparent 1px),
      linear-gradient(0deg, rgba(46,229,157,.02) 1px, transparent 1px);
    background-size: auto, 44px 44px, 44px 44px;
  }
  /* 扫描线 */
  body::after {
    content: ""; position: fixed; inset: 0; z-index: 99; pointer-events: none;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0 1px, transparent 1px 3px);
    opacity: .14;
  }
  ::selection { background: var(--radar-dim); color: #fff; }
  ::-webkit-scrollbar { width: 10px; }
  ::-webkit-scrollbar-track { background: var(--bg0); }
  ::-webkit-scrollbar-thumb { background: var(--line-hi); border-radius: 5px; }
  a { color: inherit; }
  :focus-visible { outline: 1px solid var(--radar); outline-offset: 2px; }

  /* ── 顶栏 ── */
  .topbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .radar-logo { width: 40px; height: 40px; border-radius: 50%; position: relative; flex-shrink: 0;
    border: 1px solid var(--radar-dim);
    background:
      radial-gradient(circle, var(--radar) 0 2px, transparent 2.5px),
      repeating-radial-gradient(circle, transparent 0 8px, rgba(46,229,157,.18) 8px 9px);
    box-shadow: 0 0 18px var(--radar-glow), inset 0 0 12px rgba(46,229,157,.08); }
  .radar-logo::before { content: ""; position: absolute; inset: 0; border-radius: 50%;
    background: conic-gradient(from 0deg, rgba(46,229,157,.55), transparent 70deg, transparent);
    animation: sweep 3.6s linear infinite; }
  body.crawling .radar-logo::before { animation-duration: .8s; }
  @keyframes sweep { to { transform: rotate(360deg); } }
  .brand h1 { font-family: var(--disp); font-size: 24px; font-weight: 700; letter-spacing: 1px; line-height: 1.1; }
  .brand h1 a { color: var(--ink); text-decoration: none; text-shadow: 0 0 22px var(--radar-glow); }
  .brand .sys { font-family: var(--mono); font-size: 10px; letter-spacing: 3px;
    color: var(--radar); text-transform: uppercase; opacity: .85; }
  .topbar .sub { color: var(--dim); font-size: 12px; font-family: var(--mono); }
  .searchbox { margin-left: auto; position: relative; }
  .searchbox::before { content: ">"; position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
    color: var(--radar); font-family: var(--mono); font-size: 13px; pointer-events: none; }
  .searchbox input { background: var(--bg1); border: 1px solid var(--line); color: var(--ink);
    padding: 8px 14px 8px 28px; width: 250px; font-family: var(--mono); font-size: 13px;
    clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px); }
  .searchbox input:focus { outline: none; border-color: var(--radar-dim); background: var(--bg2); }
  .searchbox input::placeholder { color: var(--faint); }

  /* ── 分区 ── */
  .sec { margin-top: 30px; animation: rise .5s .05s both; }
  .sec:nth-of-type(2) { animation-delay: .1s; } .sec:nth-of-type(3) { animation-delay: .16s; }
  .sec:nth-of-type(4) { animation-delay: .22s; } .sec:nth-of-type(5) { animation-delay: .28s; }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { .sec { animation: none; } .radar-logo::before { animation: none; } }
  .sec-title { display: flex; align-items: center; gap: 10px; margin-bottom: 12px;
    font-family: var(--disp); font-size: 15px; font-weight: 600; letter-spacing: 1px; color: var(--ink); }
  .sec-title::before { content: "//"; color: var(--radar); font-family: var(--mono); font-weight: 400; }
  .sec-title::after { content: ""; flex: 1; height: 1px;
    background: linear-gradient(90deg, var(--line-hi), transparent); }
  .sec-title .mini { margin-left: 0; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }
  @media (max-width: 980px) { .cols { grid-template-columns: 1fr; } body { padding: 18px 14px 50px; } }

  /* ── 状态卡 ── */
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 10px; margin-top: 20px; }
  .stat { position: relative; background: linear-gradient(180deg, var(--bg2), var(--bg1));
    border: 1px solid var(--line); padding: 12px 14px 10px; }
  .stat::before, .stat::after { content: ""; position: absolute; width: 8px; height: 8px; opacity: .8; }
  .stat::before { top: -1px; left: -1px; border-top: 1px solid var(--radar); border-left: 1px solid var(--radar); }
  .stat::after { bottom: -1px; right: -1px; border-bottom: 1px solid var(--radar); border-right: 1px solid var(--radar); }
  .stat b { display: block; font-family: var(--mono); font-size: 22px; font-weight: 600; color: var(--radar);
    text-shadow: 0 0 14px var(--radar-glow); line-height: 1.2; }
  .stat span { font-size: 11px; color: var(--dim); letter-spacing: 1px; }
  .stat.amber b { color: var(--amber); text-shadow: 0 0 14px var(--amber-glow); }
  .stat.amber::before { border-color: var(--amber); } .stat.amber::after { border-color: var(--amber); }
  .stat.ice b { color: var(--ice); text-shadow: 0 0 14px var(--ice-glow); }
  .stat.ice::before { border-color: var(--ice); } .stat.ice::after { border-color: var(--ice); }
  .stat.red b { color: var(--red); text-shadow: 0 0 14px var(--red-glow); }
  .stat.red::before { border-color: var(--red); } .stat.red::after { border-color: var(--red); }
  .stat.dimmed b { color: var(--dim); text-shadow: none; }
  .stat.wide b { font-size: 15px; padding-top: 5px; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--radar);
    margin-right: 6px; box-shadow: 0 0 8px var(--radar); animation: blink 2.4s infinite; vertical-align: 1px; }
  @keyframes blink { 50% { opacity: .35; } }

  /* ── 控制条 ── */
  .toolbar { display: flex; align-items: center; gap: 14px; margin-top: 14px; flex-wrap: wrap; }
  .btn { font-family: var(--disp); font-size: 14px; font-weight: 700; letter-spacing: 2px;
    background: linear-gradient(135deg, var(--radar), #19b97a); color: #04130c; border: none;
    padding: 9px 22px; cursor: pointer;
    clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px);
    transition: filter .15s, transform .15s; }
  .btn:hover { filter: brightness(1.15); transform: translateY(-1px); }
  .btn:disabled { filter: grayscale(.5) brightness(.75); cursor: wait; transform: none; animation: pulse 1.6s infinite; }
  @keyframes pulse { 50% { opacity: .65; } }
  .toolbar .hint { color: var(--dim); font-size: 12px; }
  .filters { display: flex; gap: 8px; margin-left: auto; }
  .filters a { color: var(--dim); text-decoration: none; font-size: 12px; font-family: var(--mono);
    padding: 4px 12px; border: 1px solid transparent; }
  .filters a.on { color: var(--radar); border-color: var(--radar-dim); background: var(--radar-glow); }
  .filters a:hover { color: var(--ink); }

  /* ── 图表面板 ── */
  .panel { background: var(--bg1); border: 1px solid var(--line); padding: 16px 18px; position: relative; }
  .panel::before { content: ""; position: absolute; top: -1px; left: -1px; width: 10px; height: 10px;
    border-top: 1px solid var(--radar); border-left: 1px solid var(--radar); opacity: .7; }
  .panel h3 { font-family: var(--mono); font-size: 11px; letter-spacing: 2px; color: var(--dim);
    text-transform: uppercase; margin-bottom: 12px; }
  .chart-svg { width: 100%; height: auto; display: block; }
  .legend { font-size: 11px; color: var(--dim); font-family: var(--mono); margin-top: 8px; }
  .legend i { display: inline-block; width: 9px; height: 9px; margin: 0 5px 0 14px; vertical-align: -1px; }
  .legend i:first-child { margin-left: 0; }
  /* 横向条形 */
  .hbar { display: grid; grid-template-columns: 70px 1fr 34px; align-items: center; gap: 10px; margin: 7px 0; }
  .hbar .lbl { font-size: 12px; color: var(--ink); text-align: right; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
  .hbar .track { height: 9px; background: var(--bg2); position: relative; overflow: hidden; }
  .hbar .fill { position: absolute; inset: 0 auto 0 0; background: linear-gradient(90deg, var(--radar-dim), var(--radar));
    box-shadow: 0 0 10px var(--radar-glow); animation: grow .8s .2s both cubic-bezier(.2,.8,.3,1); }
  .hbar.amber .fill { background: linear-gradient(90deg, #b97c1e, var(--amber)); box-shadow: 0 0 10px var(--amber-glow); }
  .hbar.ice .fill { background: linear-gradient(90deg, #2a7aab, var(--ice)); box-shadow: 0 0 10px var(--ice-glow); }
  @keyframes grow { from { transform: scaleX(0); transform-origin: left; } }
  .hbar .num { font-family: var(--mono); font-size: 12px; color: var(--dim); text-align: right; }
  /* 游戏占比分割条 */
  .split { display: flex; height: 26px; overflow: hidden; font-family: var(--mono); font-size: 11px; }
  .split > div { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 56px;
    transition: width .8s cubic-bezier(.2,.8,.3,1); }
  .split .g0 { background: linear-gradient(135deg, rgba(46,229,157,.28), rgba(46,229,157,.1));
    border: 1px solid var(--radar-dim); color: var(--radar); }
  .split .g1 { background: linear-gradient(135deg, rgba(255,182,72,.26), rgba(255,182,72,.08));
    border: 1px solid #8a6020; color: var(--amber); }
  /* 情报源矩阵 */
  .matrix { width: 100%; border-collapse: collapse; font-size: 12px; }
  .matrix td { padding: 5px 6px; border-bottom: 1px solid var(--line); }
  .matrix td.n { font-family: var(--mono); color: var(--dim); text-align: right; white-space: nowrap; }
  .matrix .track { height: 7px; background: var(--bg2); min-width: 60px; }
  .matrix .fill { height: 100%; background: linear-gradient(90deg, var(--radar-dim), var(--radar)); }

  /* ── 情报卡 ── */
  .card { background: var(--bg1); border: 1px solid var(--line); border-left: 3px solid var(--line-hi);
    padding: 12px 16px; margin-bottom: 10px; position: relative; transition: border-color .15s, transform .15s, box-shadow .15s; }
  .card:hover { border-color: var(--line-hi); border-left-color: var(--radar);
    transform: translateY(-1px); box-shadow: 0 6px 22px rgba(0,0,0,.45); }
  .card.leak { border-left-color: var(--amber); }
  .card.strength { border-left-color: var(--ice); }
  .card .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .card a.title { color: var(--ink); font-weight: 600; text-decoration: none; }
  .card a.title:hover { color: var(--radar); }
  a.ext { color: var(--faint); text-decoration: none; font-size: 12px; font-family: var(--mono); }
  a.ext:hover { color: var(--radar); }
  .badge { font-size: 11px; padding: 1px 8px; white-space: nowrap; font-family: var(--mono); }
  .badge.forum { background: var(--bg2); border: 1px solid var(--line); color: var(--dim); }
  .badge.del { background: var(--red); color: #fff; }
  .badge.tag { border: 1px solid #2a566e; color: var(--ice); }
  .badge.sig { border: 1px solid #7a5a1e; color: var(--amber); }
  .badge.ok { border: 1px solid var(--radar-dim); color: var(--radar); }
  .summary { color: #9fb4ad; margin-top: 5px; }
  .meta { color: var(--faint); font-size: 12px; margin-top: 5px; font-family: var(--mono); }
  /* 置信度能量条 */
  .meter { display: inline-flex; gap: 2px; align-items: center; vertical-align: -1px; }
  .meter i { width: 5px; height: 10px; background: var(--bg2); border: 1px solid var(--line); }
  .meter i.f { border: none; }
  .meter.hi i.f { background: var(--radar); box-shadow: 0 0 5px var(--radar-glow); }
  .meter.mid i.f { background: var(--amber); box-shadow: 0 0 5px var(--amber-glow); }
  .meter.lo i.f { background: var(--red); box-shadow: 0 0 5px var(--red-glow); }
  .meter-lbl { font-family: var(--mono); font-size: 11px; color: var(--dim); margin-left: 4px; }
  /* 推荐卡 */
  .card.focus { background: linear-gradient(180deg, #131711, var(--bg1)); border-color: #3d4a28;
    border-left: 3px solid var(--radar); }
  .card.focus:hover { box-shadow: 0 6px 26px rgba(46,229,157,.08); }
  .score { font-family: var(--mono); font-size: 12px; font-weight: 700; color: #04130c;
    background: var(--radar); padding: 1px 8px;
    clip-path: polygon(5px 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%, 0 5px); }
  .card.readed { opacity: .38; }
  a.mini, button.rd { font-size: 11px; font-family: var(--mono); color: var(--dim); background: none;
    border: 1px solid var(--line); padding: 1px 9px; cursor: pointer; text-decoration: none; font-weight: 400; }
  a.mini:hover, button.rd:hover { color: var(--radar); border-color: var(--radar-dim); }
  button.rd { margin-left: auto; flex-shrink: 0; }
  .empty { color: var(--faint); padding: 22px; text-align: center; border: 1px dashed var(--line);
    font-family: var(--mono); font-size: 12px; }

  /* ── 表格（日志）── */
  table.log { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--mono); }
  table.log th, table.log td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
  table.log th { color: var(--faint); font-weight: normal; letter-spacing: 1px; font-size: 11px; }
  table.log td.err { color: var(--red); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  table.log .ok-dot { color: var(--radar); }

  /* ── 楼层（详情页）── */
  .floor { border-left: 2px solid var(--line); padding: 9px 14px; margin: 8px 0; background: var(--bg1); }
  .floor.sig { border-left-color: var(--radar); background: linear-gradient(90deg, rgba(46,229,157,.05), var(--bg1) 40%); }
  .floor .who { color: var(--dim); font-size: 12px; margin-bottom: 3px; font-family: var(--mono); }
  .floor .txt { white-space: pre-wrap; word-break: break-word; }

  footer { color: var(--faint); font-size: 11px; margin-top: 40px; text-align: center;
    font-family: var(--mono); letter-spacing: 1px; }
"""

FONT_LINKS = """
<link rel="preconnect" href="https://fonts.loli.net">
<link href="https://fonts.loli.net/css2?family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
"""

HEADER = """
<div class="topbar">
  <div class="radar-logo" aria-hidden="true"></div>
  <div class="brand">
    <h1><a href="/">游戏雷达</a></h1>
    <div class="sys">GAME RADAR // TIEBA SIGNAL INTEL</div>
  </div>
  <span class="sub">{{ subtitle }}</span>
  <form class="searchbox" action="/search" method="get">
    <input name="q" placeholder="全文检索情报…" value="{{ q or '' }}">
  </form>
</div>
"""

# Jinja 宏：置信度能量条
METER_MACRO = """
{% macro meter(it) -%}
<span class="meter {{ it.conf_cls }}" title="置信度 {{ it.conf }}/10">
  {%- for i in range(10) %}<i class="{{ 'f' if i < it.conf }}"></i>{% endfor -%}
</span><span class="meter-lbl">{{ it.conf }}</span>
{%- endmacro %}
"""

# 情报卡片（首页两栏 + 推荐区共用）
CARD_MACRO = """
{% macro intel_card(it, kind, focus=False) -%}
<div class="card {{ kind }} {{ 'focus' if focus }} {{ 'readed' if it.read }}">
  <div class="head">
    {% if focus %}<span class="score">{{ it.score }}</span>{% endif %}
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="/t/{{ it.tid }}">{{ it.title or '(无标题)' }}</a>
    <a class="ext" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">↗</a>
    {% if it.fresh %}<span class="badge ok">NEW·24h</span>{% endif %}
    {% if it.deleted %}<span class="badge del">已删 ⚠</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
    {% if not it.read %}<button class="rd" onclick="markRead({{ it.tid }}, this)">✓ 已读</button>{% endif %}
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ meter(it) }} · {{ it.author_name or '匿名' }} · {{ it.time }}</div>
</div>
{%- endmacro %}
"""

INDEX_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>游戏雷达</title>
""" + FONT_LINKS + """
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + METER_MACRO + CARD_MACRO + HEADER + """

<div class="stats">
  <div class="stat"><b data-count="{{ stats.threads }}">{{ stats.threads }}</b><span>帖子</span></div>
  <div class="stat"><b data-count="{{ stats.posts }}">{{ stats.posts }}</b><span>楼层</span></div>
  <div class="stat dimmed"><b data-count="{{ stats.signals }}">{{ stats.signals }}</b><span>预筛命中</span></div>
  <div class="stat amber"><b data-count="{{ stats.leaks }}">{{ stats.leaks }}</b><span>确认爆料</span></div>
  <div class="stat ice"><b data-count="{{ stats.strength }}">{{ stats.strength }}</b><span>强度结论</span></div>
  <div class="stat"><b data-count="{{ stats.unread }}">{{ stats.unread }}</b><span>未读情报</span></div>
  <div class="stat dimmed"><b data-count="{{ stats.flash_only }}">{{ stats.flash_only }}</b><span>flash 筛掉</span></div>
  <div class="stat red"><b data-count="{{ stats.deleted }}">{{ stats.deleted }}</b><span>已删帖</span></div>
  <div class="stat wide"><b><span class="dot"></span>{{ stats.last_crawl }}</b><span>最近扫描</span></div>
</div>

<div class="toolbar">
  <button id="crawlbtn" class="btn" onclick="startCrawl()">🔄 立即扫描</button>
  <span class="hint">抓取 → 预筛 → LLM 判定（约 5-10 分钟），完成后自动刷新</span>
  <div class="filters">
    {% for g in games %}
    <a href="?game={{ g }}&show={{ show }}" class="{{ 'on' if g == game else '' }}">{{ '全部' if g == 'all' else g }}</a>
    {% endfor %}
  </div>
</div>

<div class="sec">
  <div class="sec-title">智能推荐 <span style="font-size:11px;color:var(--faint);font-family:var(--mono)">未读 · 置信度+新鲜度+删帖加权</span></div>
  {% for it in focus %}
  {{ intel_card(it, it.topic, focus=True) }}
  {% else %}
  <div class="empty">未读情报全部清空 ✦ 点「立即扫描」拉新一轮</div>
  {% endfor %}
</div>

<div class="sec">
  <div class="cols">
    <div class="panel">
      <h3>近 14 天信号量</h3>
      {{ chart_svg | safe }}
      <div class="legend"><i style="background:var(--radar-dim)"></i>新帖<i style="background:var(--amber)"></i>确认情报</div>
    </div>
    <div>
      <div class="panel" style="margin-bottom:18px">
        <h3>情报构成 · 按游戏</h3>
        <div class="split">
          {% for g in game_split %}
          <div class="g{{ loop.index0 }}" style="width:{{ g.pct }}%">{{ g.game }} {{ g.n }}</div>
          {% endfor %}
        </div>
        {% if tag_stats %}
        <h3 style="margin-top:16px">标签热度</h3>
        {% set tmax = tag_stats[0][1] %}
        {% for t, n in tag_stats[:7] %}
        <div class="hbar {{ 'amber' if loop.index0 % 2 else '' }}">
          <span class="lbl">{{ t }}</span>
          <span class="track"><span class="fill" style="width:{{ (n / tmax * 100) | round }}%"></span></span>
          <span class="num">{{ n }}</span>
        </div>
        {% endfor %}
        {% endif %}
      </div>
      <div class="panel">
        <h3>情报源矩阵</h3>
        <table class="matrix">
          {% for f in forum_matrix %}
          <tr>
            <td>{{ '🕵️' if f.topic == 'leak' else '⚔️' }} {{ f.name }}</td>
            <td><div class="track"><div class="fill" style="width:{{ f.pct }}%"></div></div></td>
            <td class="n">{{ f.n_intel }}/{{ f.n_threads }}</td>
          </tr>
          {% endfor %}
        </table>
      </div>
    </div>
  </div>
</div>

<div class="sec">
  <div class="cols">
    <div>
      <div class="sec-title">爆料情报
        <a class="mini" href="?game={{ game }}&show={{ 'all' if show == 'unread' else 'unread' }}">{{ '显示已读' if show == 'unread' else '只看未读' }}</a>
        <a class="mini" href="#" onclick="return markAll('leak')">本区全部已读</a>
      </div>
      {% for it in leaks %}
      {{ intel_card(it, 'leak') }}
      {% else %}
      <div class="empty">{{ '未读爆料清空 ✦' if show == 'unread' else '暂无确认爆料' }}</div>
      {% endfor %}
    </div>
    <div>
      <div class="sec-title">强度结论
        <a class="mini" href="?game={{ game }}&show={{ 'all' if show == 'unread' else 'unread' }}">{{ '显示已读' if show == 'unread' else '只看未读' }}</a>
        <a class="mini" href="#" onclick="return markAll('strength')">本区全部已读</a>
      </div>
      {% for it in strengths %}
      {{ intel_card(it, 'strength') }}
      {% else %}
      <div class="empty">{{ '未读强度结论清空 ✦' if show == 'unread' else '暂无强度结论' }}</div>
      {% endfor %}
    </div>
  </div>
</div>

<div class="sec">
  <div class="cols">
    <div>
      <div class="sec-title">删帖监控 <span style="font-size:11px;color:var(--faint);font-family:var(--mono)">被删的帖往往说明爆料是真的</span></div>
      {% for it in deleted %}
      <div class="card" style="border-left-color:var(--red)">
        <div class="head">
          <span class="badge forum">{{ it.forum_kw }}</span>
          <a class="title" href="/t/{{ it.tid }}">{{ it.title or '(无标题)' }}</a>
          <span class="badge del">删于 {{ it.del_time }}</span>
        </div>
        {% if it.llm_summary %}<div class="summary">{{ it.llm_summary }}</div>{% endif %}
        <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}（快照已留存）</div>
      </div>
      {% else %}
      <div class="empty">暂无删帖记录</div>
      {% endfor %}
    </div>
    <div>
      <div class="sec-title">情报源排行</div>
      <div class="panel">
        {% if authors %}
        {% set amax = authors[0].n %}
        {% for a in authors %}
        <div class="hbar ice">
          <span class="lbl" title="{{ a.author_name }}">{{ a.author_name }}</span>
          <span class="track"><span class="fill" style="width:{{ (a.n / amax * 100) | round }}%"></span></span>
          <span class="num">{{ a.n }}</span>
        </div>
        {% endfor %}
        {% else %}
        <div class="empty">暂无数据</div>
        {% endif %}
      </div>
    </div>
  </div>
</div>

<div class="sec">
  <div class="sec-title">扫描日志</div>
  <table class="log">
    <tr><th>时间</th><th>吧</th><th>看到</th><th>新帖</th><th>新楼层</th><th>状态</th></tr>
    {% for r in logs %}
    <tr>
      <td>{{ r.time }}</td><td>{{ r.forum_kw or '-' }}</td>
      <td>{{ r.threads_seen }}</td><td>{{ r.threads_new }}</td><td>{{ r.posts_new }}</td>
      <td class="{{ 'err' if r.error else '' }}">{% if r.error %}{{ r.error }}{% else %}<span class="ok-dot">● OK</span>{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
</div>

<footer>GAME RADAR · SOURCE: TIEBA · JUDGE: 龙虾 (DEEPSEEK V4) · 每 5 分钟自动刷新</footer>

<script>
const GAME = "{{ game }}";

// 数字滚动
if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.querySelectorAll('[data-count]').forEach(el => {
    const target = +el.dataset.count;
    if (!target) return;
    const t0 = performance.now(), dur = 700;
    function tick(t) {
      const p = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * e);
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  });
}

async function markRead(tid, btn) {
  await fetch('/api/read/' + tid, {method: 'POST'});
  const card = btn.closest('.card');
  card.classList.add('readed');
  btn.remove();
}

function markAll(topic) {
  const label = topic === 'leak' ? '爆料' : '强度';
  if (!confirm('把当前筛选下的「' + label + '」未读全部标记为已读？')) return false;
  fetch('/api/read_all?topic=' + topic + '&game=' + encodeURIComponent(GAME), {method: 'POST'})
    .then(() => location.reload());
  return false;
}

let crawlWatching = false;

function setCrawling(on) {
  document.body.classList.toggle('crawling', on);
  const btn = document.getElementById('crawlbtn');
  btn.disabled = on;
  btn.textContent = on ? '⏳ 扫描中…（完成后自动刷新）' : '🔄 立即扫描';
}

async function startCrawl() {
  setCrawling(true);
  await fetch('/api/crawl', {method: 'POST'});
  crawlWatching = true;
  setTimeout(pollCrawl, 5000);
}

async function pollCrawl() {
  try {
    const s = await (await fetch('/api/crawl/status')).json();
    if (s.running) {
      crawlWatching = true;
      setCrawling(true);
      setTimeout(pollCrawl, 5000);
    } else if (crawlWatching) {
      location.reload();
    }
  } catch (e) {
    setTimeout(pollCrawl, 10000);
  }
}

pollCrawl();  // 页面加载时检查是否有扫描正在跑（可能是别的标签页触发的）
</script>
</body>
</html>"""

THREAD_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ t.title or t.tid }} - 游戏雷达</title>
""" + FONT_LINKS + """
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + HEADER + """

<div class="sec">
  <div class="sec-title">{{ t.title or '(无标题)' }}
    <a class="ext" href="https://tieba.baidu.com/p/{{ t.tid }}" target="_blank">贴吧原帖 ↗</a>
  </div>
  <div class="card {{ t.topic }}">
    <div class="head">
      <span class="badge forum">{{ t.forum_kw }}（{{ t.game }} / {{ t.topic }}）</span>
      {% if t.deleted_at %}<span class="badge del">已删于 {{ del_time }} ⚠</span>{% endif %}
      <span class="badge sig">快照 {{ n_snapshots }} 份</span>
      <span class="badge ok">已读 ✓</span>
      <a class="mini" href="#" onclick="return unreadThis(this)">↩ 标为未读</a>
    </div>
    <div class="meta">楼主 {{ t.author_name or '匿名' }} · 首见 {{ first_time }} · 最近活跃 {{ last_time }}</div>
    {% if t.llm_judged %}
    <div style="margin-top:8px">
      <span class="badge {{ 'ok' if t.llm_is_leak else 'forum' }}">{{ '✅ 确认情报' if t.llm_is_leak else '❌ 非情报' }} · conf {{ t.llm_confidence }}</span>
      {% if t.llm_is_bait %}<span class="badge del">疑似钓鱼/引战</span>{% endif %}
      {% for tag in tags %}<span class="badge tag">{{ tag }}</span>{% endfor %}
    </div>
    <div class="summary" style="margin-top:6px">{{ t.llm_summary }}</div>
    {% else %}
    <div class="meta" style="margin-top:8px">⏳ 尚未 LLM 判定</div>
    {% endif %}
  </div>
</div>

<div class="sec">
  <div class="sec-title">楼层记录 <span style="font-size:11px;color:var(--faint);font-family:var(--mono)">{{ posts|length }} 条 · 👑=楼主 · ⭐=预筛命中 · LLM 只判楼主楼层</span></div>
  {% for p in posts %}
  <div class="floor {{ 'sig' if p.is_op }}">
    <div class="who">
      #{{ p.floor }} · {{ p.author_name or '匿名' }} · {{ p.time }}
      {% if p.is_op %}<span class="badge ok">👑 楼主</span>{% endif %}
      {% if p.is_signal %}<span class="badge sig">⭐ {{ p.signal_reason }}</span>{% endif %}
    </div>
    <div class="txt">{{ p.content or '(空)' }}</div>
  </div>
  {% else %}
  <div class="empty">尚未抓到楼层内容</div>
  {% endfor %}
</div>

<footer><a class="ext" href="/">← 返回总览</a></footer>

<script>
function unreadThis(el) {
  fetch('/api/unread/{{ t.tid }}', {method: 'POST'})
    .then(() => { el.textContent = '已恢复未读 ✓'; });
  return false;
}
</script>
</body>
</html>"""

SEARCH_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>搜索：{{ q }} - 游戏雷达</title>
""" + FONT_LINKS + """
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + HEADER + """

<div class="sec">
  <div class="sec-title">检索「{{ q }}」 <span style="font-size:11px;color:var(--faint);font-family:var(--mono)">命中 {{ results|length }} 条楼层</span></div>
  {% for r in results %}
  <div class="card">
    <div class="head">
      <span class="badge forum">{{ r.forum_kw }}</span>
      <a class="title" href="/t/{{ r.tid }}">{{ r.title or '(无标题)' }}</a>
      <span class="badge sig">#{{ r.floor }}</span>
      {% if r.is_signal %}<span class="badge ok">⭐</span>{% endif %}
    </div>
    <div class="summary">{{ r.excerpt }}</div>
    <div class="meta">{{ r.author_name or '匿名' }} · {{ r.time }}</div>
  </div>
  {% else %}
  <div class="empty">没有命中 ✦ 换个关键词试试</div>
  {% endfor %}
</div>

<footer><a class="ext" href="/">← 返回总览</a></footer>
</body>
</html>"""


# ─────────────────────────── 数据 / 图表 ───────────────────────────

def _trend(conn, game: str) -> list[dict]:
    """近 14 天每日新帖数和确认情报数（北京时间分桶）。"""
    game_where = "" if game == "all" else "AND f.game = :game"
    rows = conn.execute(f"""
        SELECT DATE(t.first_seen, '+8 hours') AS day,
               COUNT(*) AS total,
               SUM(CASE WHEN t.llm_is_leak = 1 THEN 1 ELSE 0 END) AS hits
        FROM threads t JOIN forums f ON f.kw = t.forum_kw
        WHERE t.first_seen > DATETIME('now', '-14 days') {game_where}
        GROUP BY day
    """, {"game": game}).fetchall()
    by_day = {r["day"]: (r["total"], r["hits"] or 0) for r in rows}
    today = datetime.now(TZ_CN).date()
    days = [(today - timedelta(days=i)) for i in range(13, -1, -1)]
    out = []
    for d in days:
        total, hits = by_day.get(d.isoformat(), (0, 0))
        out.append({"label": d.strftime("%m-%d"), "total": total, "hits": hits})
    return out


def _trend_svg(trend: list[dict]) -> str:
    """把 14 天趋势渲染成内联 SVG 面积图：总量面积 + 命中柱 + 悬浮提示。"""
    W, H = 720, 200
    pl, pr, pt, pb = 34, 10, 14, 26
    pw, ph = W - pl - pr, H - pt - pb
    mx = max((d["total"] for d in trend), default=0) or 1
    n = max(len(trend), 1)
    step = pw / max(n - 1, 1)

    pts = []
    for i, d in enumerate(trend):
        x = pl + i * step
        y = pt + ph * (1 - d["total"] / mx)
        pts.append((x, y, d))

    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(pts))
    base = pt + ph
    area = f"{line} L{pts[-1][0]:.1f},{base} L{pts[0][0]:.1f},{base} Z"

    s = [f'<svg class="chart-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    s.append(
        '<defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#2ee59d" stop-opacity=".28"/>'
        '<stop offset="1" stop-color="#2ee59d" stop-opacity=".02"/></linearGradient></defs>'
    )
    # 水平网格 + Y 轴刻度
    for frac in (0.0, 0.5, 1.0):
        gy = pt + ph * (1 - frac)
        val = round(mx * frac)
        s.append(f'<line x1="{pl}" y1="{gy:.1f}" x2="{W - pr}" y2="{gy:.1f}" '
                 f'stroke="#1c2830" stroke-width="1" stroke-dasharray="3 4"/>')
        s.append(f'<text x="{pl - 6}" y="{gy + 3.5:.1f}" text-anchor="end" '
                 f'font-size="10" fill="#45565e" font-family="Consolas,monospace">{val}</text>')
    # 命中柱（在面积图下层）
    bw = min(14.0, step * 0.4)
    for x, _, d in pts:
        if d["hits"]:
            bh = ph * d["hits"] / mx
            s.append(f'<rect x="{x - bw / 2:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                     f'fill="#ffb648" opacity=".75" rx="1.5"/>')
    # 总量面积 + 折线
    s.append(f'<path d="{area}" fill="url(#ag)"/>')
    s.append(f'<path d="{line}" fill="none" stroke="#2ee59d" stroke-width="1.6" '
             f'stroke-linejoin="round" stroke-linecap="round"/>')
    # 数据点（带原生悬浮提示）
    for i, (x, y, d) in enumerate(pts):
        last = i == len(pts) - 1
        r = 3.4 if last else 2.4
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#0b1014" '
                 f'stroke="#2ee59d" stroke-width="1.4">'
                 f'<title>{d["label"]}：新帖 {d["total"]} · 确认情报 {d["hits"]}</title></circle>')
        if last:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="none" stroke="#2ee59d" '
                     f'stroke-width="1" opacity=".4"/>')
    # X 轴标签（隔天）
    for i, (x, _, d) in enumerate(pts):
        if i % 2 == (len(pts) - 1) % 2:
            s.append(f'<text x="{x:.1f}" y="{H - 8}" text-anchor="middle" font-size="10" '
                     f'fill="#45565e" font-family="Consolas,monospace">{d["label"]}</text>')
    s.append("</svg>")
    return "".join(s)


# ─────────────────────────── 路由 ───────────────────────────

@app.route("/")
def index():
    game = request.args.get("game", "all")
    show = request.args.get("show", "unread")  # unread=只看未读（默认），all=含已读
    conn = _conn()
    try:
        games = ["all"] + [r[0] for r in conn.execute(
            "SELECT DISTINCT game FROM forums ORDER BY game").fetchall()]
        game_where = "" if game == "all" else "AND f.game = :game"
        read_where = "AND t.read_at IS NULL" if show == "unread" else ""
        params = {"game": game}

        # 智能推荐：未读情报按 置信度 + 新鲜度 + 删帖 加权（删帖往往说明爆料是真的）
        focus = _rows_to_items(conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen, t.deleted_at,
                   t.llm_confidence, t.llm_summary, t.llm_tags, t.read_at, f.topic,
                   (COALESCE(t.llm_confidence, 0)
                    + CASE WHEN t.deleted_at IS NOT NULL THEN 6 ELSE 0 END
                    + CASE WHEN REPLACE(t.first_seen, 'T', ' ') > DATETIME('now', '-1 day') THEN 5
                           WHEN REPLACE(t.first_seen, 'T', ' ') > DATETIME('now', '-2 day') THEN 3
                           WHEN REPLACE(t.first_seen, 'T', ' ') > DATETIME('now', '-4 day') THEN 1
                           ELSE 0 END) AS score
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE t.llm_is_leak = 1 AND t.read_at IS NULL {game_where}
            ORDER BY score DESC, t.first_seen DESC LIMIT 8
        """, params).fetchall())
        now = datetime.now(timezone.utc)
        for it in focus:
            try:
                it["fresh"] = (now - datetime.fromisoformat(it["first_seen"])) < timedelta(hours=24)
            except (ValueError, TypeError):
                it["fresh"] = False

        leaks = _rows_to_items(conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen, t.deleted_at,
                   t.llm_confidence, t.llm_summary, t.llm_tags, t.read_at
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE f.topic = 'leak' AND t.llm_is_leak = 1 {game_where} {read_where}
            ORDER BY (t.read_at IS NULL) DESC, t.llm_confidence DESC, t.first_seen DESC LIMIT 30
        """, params).fetchall())

        strengths = _rows_to_items(conn.execute(f"""
            SELECT t.tid, t.title, t.forum_kw, t.author_name, t.first_seen, t.deleted_at,
                   t.llm_confidence, t.llm_summary, t.llm_tags, t.read_at
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE f.topic = 'strength' AND t.llm_is_leak = 1 {game_where} {read_where}
            ORDER BY (t.read_at IS NULL) DESC, t.llm_confidence DESC, t.first_seen DESC LIMIT 30
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

        # 标签统计（确认情报的 tags 汇总）
        tag_counter: Counter = Counter()
        for r in conn.execute(f"""
            SELECT t.llm_tags FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE t.llm_is_leak = 1 {game_where}
        """, params).fetchall():
            tag_counter.update(_parse_tags(r["llm_tags"]))
        tag_stats = tag_counter.most_common(12)

        # 情报源排行
        authors = [dict(r) for r in conn.execute(f"""
            SELECT t.author_name, COUNT(*) AS n,
                   AVG(t.llm_confidence) AS avg_conf,
                   GROUP_CONCAT(DISTINCT t.forum_kw) AS forums
            FROM threads t JOIN forums f ON f.kw = t.forum_kw
            WHERE t.llm_is_leak = 1 AND t.author_name IS NOT NULL {game_where}
            GROUP BY t.author_name ORDER BY n DESC, avg_conf DESC LIMIT 8
        """, params).fetchall()]

        # 情报源矩阵：每个吧的 确认情报/总帖 数
        forum_matrix = [dict(r) for r in conn.execute("""
            SELECT f.name, f.game, COALESCE(f.topic, 'leak') AS topic,
                   COUNT(t.tid) AS n_threads,
                   COALESCE(SUM(CASE WHEN t.llm_is_leak = 1 THEN 1 ELSE 0 END), 0) AS n_intel
            FROM forums f LEFT JOIN threads t ON t.forum_kw = f.kw
            GROUP BY f.kw ORDER BY n_intel DESC
        """).fetchall()]
        fmax = max((f["n_intel"] for f in forum_matrix), default=0) or 1
        for f in forum_matrix:
            f["pct"] = round(f["n_intel"] / fmax * 100)

        # 游戏占比（确认情报数）
        game_split = [dict(r) for r in conn.execute("""
            SELECT f.game, COALESCE(SUM(CASE WHEN t.llm_is_leak = 1 THEN 1 ELSE 0 END), 0) AS n
            FROM forums f LEFT JOIN threads t ON t.forum_kw = f.kw
            GROUP BY f.game ORDER BY f.game
        """).fetchall()]
        gtotal = sum(g["n"] for g in game_split) or 1
        for g in game_split:
            g["pct"] = max(round(g["n"] / gtotal * 100), 8)

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
            "pending": conn.execute(
                "SELECT COUNT(DISTINCT t.tid) FROM posts p JOIN threads t ON t.tid=p.tid "
                "WHERE p.is_signal=1 AND (t.llm_judged IS NULL OR t.llm_judged=0)").fetchone()[0],
            "unread": conn.execute(
                "SELECT COUNT(*) FROM threads "
                "WHERE llm_is_leak=1 AND read_at IS NULL").fetchone()[0],
            "flash_only": conn.execute(
                "SELECT COUNT(*) FROM threads WHERE llm_model='flash'").fetchone()[0],
            "deleted": conn.execute(
                "SELECT COUNT(*) FROM threads WHERE deleted_at IS NOT NULL").fetchone()[0],
            "last_crawl": _fmt_time(conn.execute(
                "SELECT MAX(ended_at) FROM crawl_log").fetchone()[0]),
        }

        chart_svg = _trend_svg(_trend(conn, game))
    finally:
        conn.close()

    return render_template_string(
        INDEX_TEMPLATE, stats=stats, leaks=leaks, strengths=strengths,
        deleted=deleted, logs=logs, games=games, game=game, show=show, focus=focus,
        chart_svg=chart_svg, tag_stats=tag_stats, authors=authors,
        forum_matrix=forum_matrix, game_split=game_split,
        subtitle="贴吧内鬼爆料 · 强度分析", q="")


@app.route("/t/<int:tid>")
def thread_detail(tid: int):
    conn = _conn()
    try:
        t = conn.execute("""
            SELECT t.*, f.game, COALESCE(f.topic, 'leak') AS topic
            FROM threads t JOIN forums f ON f.kw = t.forum_kw WHERE t.tid = ?
        """, (tid,)).fetchone()
        if not t:
            return "帖子不存在", 404
        # 点开详情即视为已读
        if not t["read_at"]:
            conn.execute("UPDATE threads SET read_at=? WHERE tid=?", (_now_iso(), tid))
            conn.commit()
        posts = [
            {**dict(p), "time": _fmt_time(p["first_seen"])}
            for p in conn.execute(
                "SELECT floor, author_name, content, is_signal, signal_reason, is_op, first_seen "
                "FROM posts WHERE tid=? ORDER BY floor, id", (tid,)).fetchall()
        ]
        n_snapshots = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE tid=?", (tid,)).fetchone()[0]
    finally:
        conn.close()

    return render_template_string(
        THREAD_TEMPLATE, t=dict(t), posts=posts, n_snapshots=n_snapshots,
        tags=_parse_tags(t["llm_tags"]),
        first_time=_fmt_time(t["first_seen"]), last_time=_fmt_time(t["last_seen"]),
        del_time=_fmt_time(t["deleted_at"]),
        subtitle="帖子详情", q="")


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        conn = _conn()
        try:
            rows = conn.execute("""
                SELECT t.tid, t.title, t.forum_kw, p.floor, p.author_name,
                       p.content, p.is_signal, p.first_seen
                FROM posts p JOIN threads t ON t.tid = p.tid
                WHERE p.content LIKE ?
                ORDER BY p.first_seen DESC LIMIT 50
            """, (f"%{q}%",)).fetchall()
        finally:
            conn.close()
        for r in rows:
            content = r["content"] or ""
            pos = content.find(q)
            start = max(0, pos - 60)
            excerpt = ("…" if start > 0 else "") + content[start:start + 160] + \
                      ("…" if start + 160 < len(content) else "")
            results.append({**dict(r), "excerpt": excerpt, "time": _fmt_time(r["first_seen"])})

    return render_template_string(
        SEARCH_TEMPLATE, q=q, results=results, subtitle="全文搜索")


# ─────────────────────────── API ───────────────────────────

@app.route("/api/read/<int:tid>", methods=["POST"])
def api_read(tid: int):
    conn = _conn()
    try:
        conn.execute("UPDATE threads SET read_at=? WHERE tid=?", (_now_iso(), tid))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.route("/api/unread/<int:tid>", methods=["POST"])
def api_unread(tid: int):
    conn = _conn()
    try:
        conn.execute("UPDATE threads SET read_at=NULL WHERE tid=?", (tid,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.route("/api/read_all", methods=["POST"])
def api_read_all():
    """把某区（leak/strength，可叠加 game 筛选）的未读情报全部标记已读。"""
    topic = request.args.get("topic")
    game = request.args.get("game", "all")
    where, params = "", {"now": _now_iso()}
    if topic in ("leak", "strength"):
        where += " AND topic = :topic"
        params["topic"] = topic
    if game != "all":
        where += " AND game = :game"
        params["game"] = game
    conn = _conn()
    try:
        cur = conn.execute(f"""
            UPDATE threads SET read_at = :now
            WHERE read_at IS NULL AND llm_is_leak = 1
              AND forum_kw IN (SELECT kw FROM forums WHERE 1=1 {where})
        """, params)
        conn.commit()
        n = cur.rowcount
    finally:
        conn.close()
    return {"ok": True, "marked": n}


@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    """触发一轮完整管线（抓取→预筛→LLM 判定），后台子进程跑，不阻塞页面。"""
    global _crawl_proc
    with _crawl_lock:
        if _crawl_proc is not None and _crawl_proc.poll() is None:
            return {"running": True, "started": False}
        _crawl_proc = subprocess.Popen(
            [sys.executable, "-m", "src.main"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑框
        )
    return {"running": True, "started": True}


@app.route("/api/crawl/status")
def api_crawl_status():
    running = _crawl_proc is not None and _crawl_proc.poll() is None
    return {"running": running}


def main() -> None:
    app.run(host="127.0.0.1", port=8787, debug=False)


if __name__ == "__main__":
    main()
