"""通过 WSL 里的 openclaw CLI 调用龙虾 agent 做 LLM 判定。
调用方式：openclaw agent --agent main --model <model> --json --message "<prompt>"

智能路由（省钱省时间）：
  阶段1 分类  flash 模型跑全量——判 is_leak/is_bait/confidence/tags（结构化、短输出，flash 够用）
  阶段2 总结  pro  模型只跑精华——仅对 flash 判为有价值(is_leak=1)的帖生成高质量中文 summary
大部分帖是水贴/非爆料，flash 一遍筛掉；pro 只处理少数精华，整体更快更省。
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

MODEL_FLASH = "deepseek-v4-flash"  # 分类：快、便宜
MODEL_PRO = "deepseek-v4-pro"      # 总结：质量高

# ---- 阶段1：分类提示词（flash，只出结构化 JSON，不含 summary）----
CLASSIFY_LEAK = """你是游戏爆料情报分类器，专注鸣潮和绝区零。
下面是某帖**楼主（发帖人）的全部发言**（回复区已过滤）。快速判断：

1. is_leak（bool）：是不是真实的游戏内容爆料/前瞻（非官方公布内容）？
2. is_bait（bool）：是否是钓鱼/营销/无意义水贴？
3. confidence（0-10）：你对 is_leak 判断的置信度。
4. tags（数组）：从["角色","卡池","剧情","数值","联动","武器"]中选0-3个。

【帖子信息】
吧名：{forum}
标题：{title}
楼主发言：
{content}

只输出 JSON，不要任何前后缀：
{{"is_leak": bool, "is_bait": bool, "confidence": 0-10, "tags": [...]}}"""

CLASSIFY_STRENGTH = """你是游戏强度分析分类器，专注鸣潮和绝区零。
下面是某帖**楼主（发帖人）的全部发言**（回复区已过滤）。快速判断：

1. is_leak（bool）：是不是有信息量的强度分析/配队推荐/抽卡建议（有具体结论或数据支撑，而非纯水贴）？
2. is_bait（bool）：是否是引战/钓鱼/无意义水贴？
3. confidence（0-10）：你对判断的置信度。
4. tags（数组）：从["强度","配队","抽卡建议","数值","角色","武器","深塔"]中选0-3个。

【帖子信息】
吧名：{forum}
标题：{title}
楼主发言：
{content}

只输出 JSON，不要任何前后缀：
{{"is_leak": bool, "is_bait": bool, "confidence": 0-10, "tags": [...]}}"""

CLASSIFY_BY_TOPIC = {"leak": CLASSIFY_LEAK, "strength": CLASSIFY_STRENGTH}

# ---- 阶段2：总结提示词（pro，只对有价值的帖生成一句话，纯文本）----
SUMMARIZE_LEAK = """你是游戏爆料情报分析员，专注鸣潮和绝区零。
下面是某帖楼主的发言，已确认包含爆料。用1-2句中文精准概括爆料的核心内容
（尽量具体到角色名/版本/卡池/数值等关键信息）。只输出这句话，不要任何前后缀、不要引号。

【帖子信息】
吧名：{forum}
标题：{title}
楼主发言：
{content}"""

SUMMARIZE_STRENGTH = """你是游戏强度分析情报员，专注鸣潮和绝区零。
下面是某帖楼主的发言，已确认含有价值的强度分析。用1-2句中文概括核心结论
（如"XX角色T0，建议必抽"、"XX配队深塔伤害超标"）。只输出这句话，不要任何前后缀、不要引号。

【帖子信息】
吧名：{forum}
标题：{title}
楼主发言：
{content}"""

SUMMARIZE_BY_TOPIC = {"leak": SUMMARIZE_LEAK, "strength": SUMMARIZE_STRENGTH}


def _call_openclaw(prompt: str, model: str = MODEL_PRO) -> str:
    """调用 WSL 里的 openclaw（指定模型），返回 assistant 回复文本。
    wsl.exe -- 后的参数会被 bash 重新解析，帖子正文里的反引号/引号会炸掉命令行，
    所以 prompt 走 base64（纯 ASCII 安全字符），bash 侧解码还原。"""
    b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    result = subprocess.run(
        [WSL_EXE, "-d", "Ubuntu", "--",
         OPENCLAW_BIN, "agent",
         "--agent", AGENT_ID,
         "--model", model,
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


def _lz_posts(conn: sqlite3.Connection, tid: int) -> list:
    """取楼主在本帖的全部楼层——爆料/分析主体都在楼主手里（主楼 + 楼主自己盖的补充楼），
    回复区的"求证/钓鱼/辟谣"是噪音，判定时不喂给 LLM（数据仍全量入库）。
    优先用 is_op 标记；老数据没标记时回退到「首楼作者的 author_id」，再回退「首楼+signal」。"""
    rows = conn.execute(
        "SELECT floor, author_name, content FROM posts "
        "WHERE tid=? AND is_op=1 ORDER BY floor",
        (tid,),
    ).fetchall()
    if rows:
        return rows
    op = conn.execute(
        "SELECT author_id FROM posts WHERE tid=? AND floor=1 LIMIT 1", (tid,)
    ).fetchone()
    op_id = op["author_id"] if op else None
    if op_id:
        rows = conn.execute(
            "SELECT floor, author_name, content FROM posts "
            "WHERE tid=? AND author_id=? ORDER BY floor",
            (tid, op_id),
        ).fetchall()
        if rows:
            return rows
    return conn.execute(
        "SELECT floor, author_name, content FROM posts "
        "WHERE tid=? AND (floor=1 OR is_signal=1) ORDER BY floor",
        (tid,),
    ).fetchall()


def _format_content(posts: list[dict]) -> str:
    lines = []
    for p in posts[:10]:  # 最多取前 10 楼，控制 token
        floor = p.get("floor") or "?"
        author = p.get("author_name") or "匿名"
        text = (p.get("content") or "").strip()[:300]
        lines.append(f"第{floor}楼 [{author}]: {text}")
    return "\n".join(lines)


def judge_thread(forum: str, title: str, posts: list[dict], topic: str = "leak") -> dict:
    """智能路由判定：flash 分类 → 有价值才 pro 总结。
    返回 {is_leak, is_bait, confidence, summary, tags, _model}。"""
    content = _format_content(posts)
    title = title or "(无标题)"

    # 阶段1：flash 分类
    cls_tmpl = CLASSIFY_BY_TOPIC.get(topic, CLASSIFY_LEAK)
    cls = _extract_json(_call_openclaw(
        cls_tmpl.format(forum=forum, title=title, content=content), MODEL_FLASH))
    result = {
        "is_leak": bool(cls.get("is_leak")),
        "is_bait": bool(cls.get("is_bait")),
        "confidence": cls.get("confidence"),
        "tags": cls.get("tags", []),
    }

    # 阶段2：仅对有价值的帖用 pro 生成高质量总结
    if result["is_leak"]:
        sum_tmpl = SUMMARIZE_BY_TOPIC.get(topic, SUMMARIZE_LEAK)
        try:
            summary = _call_openclaw(
                sum_tmpl.format(forum=forum, title=title, content=content), MODEL_PRO)
            summary = summary.strip().strip('"').strip("：:").strip()
            result["summary"] = summary[:200] or "（爆料，待补充摘要）"
        except Exception:
            # pro 总结失败不丢分类结果，降级用楼主正文摘录
            result["summary"] = (content.replace("\n", " ")[:80] + "…")
        result["_model"] = "flash+pro"
    else:
        result["summary"] = "非爆料" if topic == "leak" else "无有效结论"
        result["_model"] = "flash"

    return result


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
          AND COALESCE(t.llm_attempts, 0) < 3
    """).fetchall()

    judged = 0
    for row in rows:
        posts = _lz_posts(conn, row["tid"])
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
                    llm_tags = ?,
                    llm_model = ?
                WHERE tid = ?
            """, (
                1 if result.get("is_leak") else 0,
                1 if result.get("is_bait") else 0,
                result.get("confidence"),
                result.get("summary"),
                json.dumps(result.get("tags", []), ensure_ascii=False),
                result.get("_model"),
                row["tid"],
            ))
            conn.commit()
            judged += 1
            print(f"  [llm/{result.get('_model', '?')}] tid={row['tid']} "
                  f"leak={result.get('is_leak')} conf={result.get('confidence')} "
                  f"{result.get('summary', '')[:40]}")
        except Exception as e:
            conn.execute(
                "UPDATE threads SET llm_attempts = COALESCE(llm_attempts, 0) + 1 WHERE tid = ?",
                (row["tid"],),
            )
            conn.commit()
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
        ("llm_attempts",   "INTEGER NOT NULL DEFAULT 0"),  # 判定失败重试计数，3 次后放弃
        ("llm_model",      "TEXT"),                         # 实际用的模型：flash / flash+pro
    ]
    for col, typedef in additions:
        if col not in cols:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} {typedef}")
    conn.commit()
