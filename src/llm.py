"""通过 WSL 里的 openclaw CLI 调用龙虾 agent 做 LLM 判定。
调用方式：openclaw agent --agent main --json --message "<prompt>"
返回 JSON，回复文本在 result.meta.finalAssistantVisibleText。
"""
import base64
import json
import sqlite3
import subprocess
from pathlib import Path

OPENCLAW_BIN = "/home/streamax/.npm-global/bin/openclaw"
AGENT_ID = "main"
TIMEOUT_SEC = 120
# 绝对路径：当管线被 openclaw cron 从 WSL 侧拉起时，子进程 PATH 里没有 System32
WSL_EXE = r"C:\Windows\System32\wsl.exe"

LEAK_PROMPT_TMPL = """你是游戏爆料情报分析员，专注鸣潮和绝区零。
下面是贴吧内鬼/爆料吧里的一个帖子（含楼层），请判断：

1. is_leak（bool）：这是不是真实的游戏内容爆料/前瞻（非官方公布内容）？
2. is_bait（bool）：是否是钓鱼/营销/无意义水贴？
3. confidence（0-10）：你对 is_leak 判断的置信度。
4. summary（中文，1-2句）：如果是爆料，一句话概括爆料内容；不是就写"非爆料"。
5. tags（数组）：内容标签，如["角色","卡池","剧情","数值","联动","武器"]中选0-3个。

【帖子信息】
吧名：{forum}
标题：{title}
楼层内容：
{content}

直接输出 JSON，不要任何前后缀，格式：
{{"is_leak": bool, "is_bait": bool, "confidence": 0-10, "summary": "...", "tags": [...]}}"""

# 强度吧：is_leak 字段在这里的语义是"有信息量的强度分析"，复用同一套 DB 字段
STRENGTH_PROMPT_TMPL = """你是游戏强度分析情报员，专注鸣潮和绝区零。
下面是贴吧强度吧里的一个帖子（含楼层），请判断：

1. is_leak（bool）：这是不是有信息量的强度分析/配队推荐/抽卡建议（有具体结论或数据支撑，而非纯水贴）？
2. is_bait（bool）：是否是引战/钓鱼/无意义水贴？
3. confidence（0-10）：你对判断的置信度。
4. summary（中文，1-2句）：概括核心结论（如"XX角色T0，建议必抽"、"XX配队深塔伤害超标"）；没有有效结论就写"无有效结论"。
5. tags（数组）：内容标签，从["强度","配队","抽卡建议","数值","角色","武器","深塔"]中选0-3个。

【帖子信息】
吧名：{forum}
标题：{title}
楼层内容：
{content}

直接输出 JSON，不要任何前后缀，格式：
{{"is_leak": bool, "is_bait": bool, "confidence": 0-10, "summary": "...", "tags": [...]}}"""

PROMPT_BY_TOPIC = {
    "leak": LEAK_PROMPT_TMPL,
    "strength": STRENGTH_PROMPT_TMPL,
}


def _call_openclaw(prompt: str) -> str:
    """调用 WSL 里的 openclaw，返回 assistant 回复文本。
    wsl.exe -- 后的参数会被 bash 重新解析，帖子正文里的反引号/引号会炸掉命令行，
    所以 prompt 走 base64（纯 ASCII 安全字符），bash 侧解码还原。"""
    b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    result = subprocess.run(
        [WSL_EXE, "-d", "Ubuntu", "--",
         OPENCLAW_BIN, "agent",
         "--agent", AGENT_ID,
         "--json",
         "--timeout", str(TIMEOUT_SEC),
         "--message", f'"$(echo {b64} | base64 -d)"'],
        capture_output=True, text=True, timeout=TIMEOUT_SEC + 30,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"openclaw 退出码 {result.returncode}: {result.stderr[:300]}")
    run_result = json.loads(result.stdout)
    text = (
        run_result.get("result", {}).get("meta", {}).get("finalAssistantVisibleText")
        or run_result.get("result", {}).get("meta", {}).get("finalAssistantRawText")
        or run_result.get("finalAssistantVisibleText")
        or ""
    )
    if not text:
        raise RuntimeError(f"openclaw 响应中未找到 finalAssistantVisibleText: {result.stdout[:300]}")
    return text


def _extract_json(text: str) -> dict:
    s = text.strip()
    s = s.replace("```json", "").replace("```", "").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"响应中未找到 JSON: {s[:200]}")
    return json.loads(s[start:end + 1])


def judge_thread(forum: str, title: str, posts: list[dict], topic: str = "leak") -> dict:
    """对一个帖子（含多楼层）做综合判定，返回 {is_leak, is_bait, confidence, summary, tags}。
    topic 决定用哪套提示词：leak=爆料判定，strength=强度分析判定。"""
    content_lines = []
    for p in posts[:10]:  # 最多取前 10 楼，控制 token
        floor = p.get("floor") or "?"
        author = p.get("author_name") or "匿名"
        text = (p.get("content") or "").strip()[:300]
        content_lines.append(f"第{floor}楼 [{author}]: {text}")
    content = "\n".join(content_lines)
    tmpl = PROMPT_BY_TOPIC.get(topic, LEAK_PROMPT_TMPL)
    prompt = tmpl.format(forum=forum, title=title or "(无标题)", content=content)
    raw = _call_openclaw(prompt)
    return _extract_json(raw)


def run(conn: sqlite3.Connection, db_path: Path) -> int:
    """对所有 is_signal=1 且未经 LLM 判定的帖子跑判定，结果写回 threads 表。"""
    # 先确保 threads 表有 llm 相关字段
    _ensure_llm_cols(conn)

    rows = conn.execute("""
        SELECT DISTINCT t.tid, t.title, t.forum_kw,
               f.name as forum_name,
               COALESCE(f.topic, 'leak') as topic
        FROM posts p
        JOIN threads t ON t.tid = p.tid
        JOIN forums f ON f.kw = t.forum_kw
        WHERE p.is_signal = 1
          AND (t.llm_judged IS NULL OR t.llm_judged = 0)
    """).fetchall()

    judged = 0
    for row in rows:
        posts = conn.execute(
            "SELECT floor, author_name, content FROM posts WHERE tid=? AND is_signal=1 ORDER BY floor",
            (row["tid"],)
        ).fetchall()
        try:
            result = judge_thread(row["forum_name"], row["title"], [dict(p) for p in posts],
                                  topic=row["topic"])
            conn.execute("""
                UPDATE threads SET
                    llm_judged = 1,
                    llm_is_leak = ?,
                    llm_is_bait = ?,
                    llm_confidence = ?,
                    llm_summary = ?,
                    llm_tags = ?
                WHERE tid = ?
            """, (
                1 if result.get("is_leak") else 0,
                1 if result.get("is_bait") else 0,
                result.get("confidence"),
                result.get("summary"),
                json.dumps(result.get("tags", []), ensure_ascii=False),
                row["tid"],
            ))
            conn.commit()
            judged += 1
            print(f"  [llm] tid={row['tid']} leak={result.get('is_leak')} "
                  f"conf={result.get('confidence')} {result.get('summary', '')[:40]}")
        except Exception as e:
            print(f"  [llm] tid={row['tid']} 判定失败: {e}")
    return judged


def _ensure_llm_cols(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
    additions = [
        ("llm_judged",     "INTEGER NOT NULL DEFAULT 0"),
        ("llm_is_leak",    "INTEGER"),
        ("llm_is_bait",    "INTEGER"),
        ("llm_confidence", "INTEGER"),
        ("llm_summary",    "TEXT"),
        ("llm_tags",       "TEXT"),
    ]
    for col, typedef in additions:
        if col not in cols:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} {typedef}")
    conn.commit()
