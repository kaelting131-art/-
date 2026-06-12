"""游戏雷达 Web 仪表盘。本地只读看板，展示已判定的爆料/强度情报。
启动：python -m src.web   →  http://127.0.0.1:8787
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


def _rows_to_items(rows) -> list[dict]:
    items = []
    for r in rows:
        d = dict(r)
        d["tags"] = _parse_tags(d.get("llm_tags"))
        d["time"] = _fmt_time(d.get("first_seen"))
        d["deleted"] = bool(d.get("deleted_at"))
        d["read"] = bool(d.get("read_at"))
        items.append(d)
    return items


BASE_CSS = """
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
  h1 a { color: var(--text); text-decoration: none; }
  h1 .sub { color: var(--dim); font-size: 13px; font-weight: normal; margin-left: 8px; }
  h2 { font-size: 15px; margin: 28px 0 10px; color: var(--accent); }
  .topbar { display: flex; align-items: center; justify-content: space-between;
            flex-wrap: wrap; gap: 10px; }
  .searchbox input { background: var(--panel); border: 1px solid var(--border);
                     color: var(--text); border-radius: 8px; padding: 7px 12px; width: 240px; }
  .searchbox input:focus { outline: none; border-color: var(--accent); }
  .chips { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 4px; }
  .chip { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
          padding: 8px 14px; text-align: center; min-width: 88px; }
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
  a.ext { color: var(--dim); text-decoration: none; font-size: 12px; }
  a.ext:hover { color: var(--accent); }
  .badge { font-size: 11px; border-radius: 5px; padding: 1px 7px; white-space: nowrap; }
  .badge.forum { background: #20253244; border: 1px solid var(--border); color: var(--dim); }
  .badge.conf { border: 1px solid var(--ok); color: var(--ok); }
  .badge.del { background: var(--leak); color: #fff; }
  .badge.tag { border: 1px solid var(--strength); color: var(--strength); }
  .badge.sig { border: 1px solid var(--accent); color: var(--accent); }
  .summary { color: var(--dim); margin-top: 4px; }
  .meta { color: var(--dim); font-size: 12px; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--dim); font-weight: normal; }
  td.err { color: var(--leak); max-width: 320px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
  .empty { color: var(--dim); padding: 16px; text-align: center; }
  footer { color: var(--dim); font-size: 12px; margin-top: 28px; text-align: center; }
  /* 趋势图 */
  .trend { display: flex; align-items: flex-end; gap: 6px; height: 110px;
           background: var(--panel); border: 1px solid var(--border);
           border-radius: 10px; padding: 14px 16px 26px; position: relative; }
  .trend .day { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
                align-items: center; height: 100%; position: relative; }
  .trend .bar { width: 70%; max-width: 26px; background: #3a4154; border-radius: 3px 3px 0 0;
                display: flex; flex-direction: column; justify-content: flex-end; }
  .trend .bar .hit { background: var(--accent); border-radius: 3px 3px 0 0; width: 100%; }
  .trend .lbl { position: absolute; bottom: -22px; font-size: 10px; color: var(--dim);
                white-space: nowrap; }
  .trend .num { font-size: 10px; color: var(--dim); margin-bottom: 2px; }
  .legend { font-size: 12px; color: var(--dim); margin-top: 6px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
              margin: 0 4px 0 12px; vertical-align: -1px; }
  /* 标签云 */
  .tags { display: flex; gap: 8px; flex-wrap: wrap; }
  .tags .t { background: var(--panel); border: 1px solid var(--strength);
             color: var(--strength); border-radius: 14px; padding: 3px 12px; font-size: 13px; }
  .tags .t b { color: var(--text); margin-left: 4px; }
  /* 楼层 */
  .floor { border-left: 3px solid var(--border); padding: 8px 14px; margin: 8px 0; }
  .floor.sig { border-left-color: var(--accent); }
  .floor .who { color: var(--dim); font-size: 12px; margin-bottom: 2px; }
  .floor .txt { white-space: pre-wrap; word-break: break-word; }
  /* 工具栏 / 已读 / 智能推荐 */
  .toolbar { display: flex; align-items: center; gap: 12px; margin: 12px 0 4px; flex-wrap: wrap; }
  .toolbar button { background: var(--accent); color: #1a1505; border: none; border-radius: 8px;
                    padding: 7px 16px; font-weight: 600; cursor: pointer; font-size: 13px; }
  .toolbar button:disabled { opacity: .55; cursor: wait; }
  .toolbar .hint { color: var(--dim); font-size: 12px; }
  a.mini, button.rd { font-size: 12px; font-weight: normal; color: var(--dim); background: none;
                      border: 1px solid var(--border); border-radius: 6px; padding: 1px 8px;
                      cursor: pointer; text-decoration: none; }
  a.mini:hover, button.rd:hover { color: var(--accent); border-color: var(--accent); }
  button.rd { margin-left: auto; flex-shrink: 0; }
  .card.readed { opacity: .4; }
  .card.focus { border-color: #6a5524; background: #1d1a13; }
  .score { color: var(--accent); font-size: 12px; font-weight: 700; }
"""

HEADER = """
<div class="topbar">
  <h1><a href="/">🎮 游戏雷达</a> <span class="sub">{{ subtitle }}</span></h1>
  <form class="searchbox" action="/search" method="get">
    <input name="q" placeholder="搜索全部楼层内容…" value="{{ q or '' }}">
  </form>
</div>
"""

INDEX_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>游戏雷达</title>
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + HEADER + """

<div class="chips">
  <div class="chip"><b>{{ stats.threads }}</b><span>帖子</span></div>
  <div class="chip"><b>{{ stats.posts }}</b><span>楼层</span></div>
  <div class="chip"><b>{{ stats.signals }}</b><span>预筛命中</span></div>
  <div class="chip"><b>{{ stats.leaks }}</b><span>确认爆料</span></div>
  <div class="chip"><b>{{ stats.strength }}</b><span>强度结论</span></div>
  <div class="chip"><b>{{ stats.unread }}</b><span>未读情报</span></div>
  <div class="chip"><b>{{ stats.pending }}</b><span>待判定</span></div>
  <div class="chip"><b>{{ stats.flash_only }}</b><span>flash筛掉</span></div>
  <div class="chip"><b>{{ stats.deleted }}</b><span>已删帖</span></div>
  <div class="chip"><b>{{ stats.last_crawl }}</b><span>最近抓取</span></div>
</div>

<div class="filters">
  {% for g in games %}
  <a href="?game={{ g }}&show={{ show }}" class="{{ 'on' if g == game else '' }}">{{ '全部' if g == 'all' else g }}</a>
  {% endfor %}
</div>

<div class="toolbar">
  <button id="crawlbtn" onclick="startCrawl()">🔄 立即抓取</button>
  <span class="hint">手动触发一轮：抓取 → 预筛 → LLM 判定（约 5-10 分钟），完成后自动刷新</span>
</div>

<h2>🔥 智能推荐（未读 · 置信度 + 新鲜度 + 删帖加权）</h2>
{% for it in focus %}
<div class="card focus">
  <div class="head">
    <span class="score">{{ it.score }}分</span>
    <span class="badge forum">{{ '🕵️' if it.topic == 'leak' else '⚔️' }} {{ it.forum_kw }}</span>
    <a class="title" href="/t/{{ it.tid }}">{{ it.title or '(无标题)' }}</a>
    <a class="ext" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">贴吧↗</a>
    <span class="badge conf">conf {{ it.llm_confidence }}</span>
    {% if it.fresh %}<span class="badge sig">🆕 24h</span>{% endif %}
    {% if it.deleted %}<span class="badge del">已删 ⚠️</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
    <button class="rd" onclick="markRead({{ it.tid }}, this)">✓ 已读</button>
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}</div>
</div>
{% else %}
<div class="empty">未读情报全部看完了 ✨（点「立即抓取」拉一轮新的）</div>
{% endfor %}

<h2>📈 近 14 天动态</h2>
<div class="trend">
  {% for d in trend %}
  <div class="day">
    <div class="num">{{ d.total if d.total else '' }}</div>
    <div class="bar" style="height: {{ d.h }}%">
      <div class="hit" style="height: {{ d.hit_pct }}%"></div>
    </div>
    <div class="lbl">{{ d.label }}</div>
  </div>
  {% endfor %}
</div>
<div class="legend">柱高 = 新帖数<i style="background:#3a4154"></i>新帖<i style="background:var(--accent)"></i>其中确认情报</div>

{% if tag_stats %}
<h2>🏷️ 情报标签分布</h2>
<div class="tags">
  {% for t, n in tag_stats %}<span class="t">{{ t }}<b>{{ n }}</b></span>{% endfor %}
</div>
{% endif %}

{% if authors %}
<h2>👤 情报源排行（确认情报的楼主）</h2>
<table>
  <tr><th>楼主</th><th>确认情报数</th><th>平均置信度</th><th>主要活跃吧</th></tr>
  {% for a in authors %}
  <tr>
    <td>{{ a.author_name }}</td><td>{{ a.n }}</td>
    <td>{{ '%.1f' % a.avg_conf }}</td><td>{{ a.forums }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}

<h2>🕵️ 爆料情报（{{ '未读' if show == 'unread' else '全部' }} · 置信度排序）
  <a class="mini" href="?game={{ game }}&show={{ 'all' if show == 'unread' else 'unread' }}">{{ '显示已读' if show == 'unread' else '只看未读' }}</a>
  <a class="mini" href="#" onclick="return markAll('leak')">本区全部已读</a>
</h2>
{% for it in leaks %}
<div class="card {{ 'readed' if it.read }}">
  <div class="head">
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="/t/{{ it.tid }}">{{ it.title or '(无标题)' }}</a>
    <a class="ext" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">贴吧↗</a>
    <span class="badge conf">conf {{ it.llm_confidence }}</span>
    {% if it.deleted %}<span class="badge del">已删 ⚠️</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
    {% if not it.read %}<button class="rd" onclick="markRead({{ it.tid }}, this)">✓ 已读</button>{% endif %}
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}</div>
</div>
{% else %}
<div class="empty">{{ '未读爆料清空了 ✨' if show == 'unread' else '暂无确认爆料' }}</div>
{% endfor %}

<h2>⚔️ 强度结论（{{ '未读' if show == 'unread' else '全部' }} · 置信度排序）
  <a class="mini" href="?game={{ game }}&show={{ 'all' if show == 'unread' else 'unread' }}">{{ '显示已读' if show == 'unread' else '只看未读' }}</a>
  <a class="mini" href="#" onclick="return markAll('strength')">本区全部已读</a>
</h2>
{% for it in strengths %}
<div class="card {{ 'readed' if it.read }}">
  <div class="head">
    <span class="badge forum">{{ it.forum_kw }}</span>
    <a class="title" href="/t/{{ it.tid }}">{{ it.title or '(无标题)' }}</a>
    <a class="ext" href="https://tieba.baidu.com/p/{{ it.tid }}" target="_blank">贴吧↗</a>
    <span class="badge conf">conf {{ it.llm_confidence }}</span>
    {% if it.deleted %}<span class="badge del">已删 ⚠️</span>{% endif %}
    {% for t in it.tags %}<span class="badge tag">{{ t }}</span>{% endfor %}
    {% if not it.read %}<button class="rd" onclick="markRead({{ it.tid }}, this)">✓ 已读</button>{% endif %}
  </div>
  <div class="summary">{{ it.llm_summary }}</div>
  <div class="meta">{{ it.author_name or '匿名' }} · 首见 {{ it.time }}</div>
</div>
{% else %}
<div class="empty">{{ '未读强度结论清空了 ✨' if show == 'unread' else '暂无强度结论' }}</div>
{% endfor %}

<h2>🗑️ 删帖监控（被删的帖往往说明爆料是真的）</h2>
{% for it in deleted %}
<div class="card">
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

<script>
const GAME = "{{ game }}";

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

async function startCrawl() {
  const btn = document.getElementById('crawlbtn');
  btn.disabled = true;
  btn.textContent = '⏳ 抓取中…（完成后自动刷新）';
  await fetch('/api/crawl', {method: 'POST'});
  crawlWatching = true;
  setTimeout(pollCrawl, 5000);
}

async function pollCrawl() {
  try {
    const s = await (await fetch('/api/crawl/status')).json();
    if (s.running) {
      crawlWatching = true;
      const btn = document.getElementById('crawlbtn');
      btn.disabled = true;
      btn.textContent = '⏳ 抓取中…（完成后自动刷新）';
      setTimeout(pollCrawl, 5000);
    } else if (crawlWatching) {
      location.reload();
    }
  } catch (e) {
    setTimeout(pollCrawl, 10000);
  }
}

pollCrawl();  // 页面加载时检查是否有抓取正在跑（可能是别的标签页触发的）
</script>
</body>
</html>"""

THREAD_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ t.title or t.tid }} - 游戏雷达</title>
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + HEADER + """

<h2 style="margin-top:20px">
  {{ t.title or '(无标题)' }}
  <a class="ext" href="https://tieba.baidu.com/p/{{ t.tid }}" target="_blank">贴吧原帖↗</a>
</h2>
<div class="card">
  <div class="head">
    <span class="badge forum">{{ t.forum_kw }}（{{ t.game }} / {{ t.topic }}）</span>
    {% if t.deleted_at %}<span class="badge del">已删于 {{ del_time }} ⚠️</span>{% endif %}
    <span class="badge sig">快照 {{ n_snapshots }} 份</span>
    <span class="badge conf">已读 ✓</span>
    <a class="mini" href="#" onclick="return unreadThis(this)">↩ 标为未读</a>
  </div>
  <div class="meta">楼主 {{ t.author_name or '匿名' }} · 首见 {{ first_time }} · 最近活跃 {{ last_time }}</div>
  {% if t.llm_judged %}
  <div style="margin-top:8px">
    <span class="badge conf">{{ '✅ 确认情报' if t.llm_is_leak else '❌ 非情报' }} · conf {{ t.llm_confidence }}</span>
    {% if t.llm_is_bait %}<span class="badge del">疑似钓鱼/引战</span>{% endif %}
    {% for tag in tags %}<span class="badge tag">{{ tag }}</span>{% endfor %}
  </div>
  <div class="summary" style="margin-top:6px">{{ t.llm_summary }}</div>
  {% else %}
  <div class="meta" style="margin-top:8px">⏳ 尚未 LLM 判定</div>
  {% endif %}
</div>

<h2>💬 楼层（{{ posts|length }} 条 · 👑=楼主 · ⭐=预筛命中 · LLM 只判楼主楼层）</h2>
{% for p in posts %}
<div class="floor {{ 'sig' if p.is_op }}">
  <div class="who">
    {{ p.floor }}楼 · {{ p.author_name or '匿名' }} · {{ p.time }}
    {% if p.is_op %}<span class="badge sig">👑 楼主</span>{% endif %}
    {% if p.is_signal %}<span class="badge tag">⭐ {{ p.signal_reason }}</span>{% endif %}
  </div>
  <div class="txt">{{ p.content or '(空)' }}</div>
</div>
{% else %}
<div class="empty">尚未抓到楼层内容</div>
{% endfor %}

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
<style>""" + BASE_CSS + """</style>
</head>
<body>
""" + HEADER + """

<h2>🔍 「{{ q }}」命中 {{ results|length }} 条楼层</h2>
{% for r in results %}
<div class="card">
  <div class="head">
    <span class="badge forum">{{ r.forum_kw }}</span>
    <a class="title" href="/t/{{ r.tid }}">{{ r.title or '(无标题)' }}</a>
    <span class="badge sig">{{ r.floor }}楼</span>
    {% if r.is_signal %}<span class="badge sig">⭐</span>{% endif %}
  </div>
  <div class="summary">{{ r.excerpt }}</div>
  <div class="meta">{{ r.author_name or '匿名' }} · {{ r.time }}</div>
</div>
{% else %}
<div class="empty">没有命中。换个关键词试试？</div>
{% endfor %}

<footer><a class="ext" href="/">← 返回总览</a></footer>
</body>
</html>"""


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
    max_total = max((by_day.get(d.isoformat(), (0, 0))[0] for d in days), default=0) or 1
    out = []
    for d in days:
        total, hits = by_day.get(d.isoformat(), (0, 0))
        out.append({
            "label": d.strftime("%m-%d"),
            "total": total,
            "h": round(total / max_total * 100) if total else 2,
            "hit_pct": round(hits / total * 100) if total else 0,
        })
    return out


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

        trend = _trend(conn, game)
    finally:
        conn.close()

    return render_template_string(
        INDEX_TEMPLATE, stats=stats, leaks=leaks, strengths=strengths,
        deleted=deleted, logs=logs, games=games, game=game, show=show, focus=focus,
        trend=trend, tag_stats=tag_stats, authors=authors,
        subtitle="贴吧内鬼爆料 · 强度分析 · 每 5 分钟自动刷新", q="")


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
