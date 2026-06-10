"""关键词预筛。对 posts 表里 is_signal=0 的新帖跑一遍，命中则标记 is_signal=1。
这一步只做粗筛，目的是把 LLM 调用量压到 5-10%。
"""
import sqlite3

# 触发词：出现即值得关注，宁可错杀
TRIGGER_WORDS = [
    "爆料", "内鬼", "前瞻", "下个版本", "下版本",
    "新角色", "新武器", "up池", "卡池", "数值",
    "技能", "立绘", "5星", "五星", "限定", "复刻",
    "泄露", "流出", "内部", "测试服", "beta", "leak",
    "前瞻", "版本号", "未公开", "提前",
]

# 反向词：出现则降权（即使命中触发词也取消标记）
NOISE_WORDS = [
    "钓鱼", "营销号", "月经贴", "引战", "辟谣",
    "是真的吗", "有图吗", "水贴", "转载", "搬运",
    "已删", "求证",
]


def _check(text: str) -> tuple[bool, str]:
    """返回 (is_signal, 命中的触发词逗号分隔)"""
    if not text:
        return False, ""
    low = text.lower()
    hits = [w for w in TRIGGER_WORDS if w.lower() in low]
    if not hits:
        return False, ""
    noise = [w for w in NOISE_WORDS if w.lower() in low]
    if noise:
        return False, ""
    return True, ",".join(hits)


def run(conn: sqlite3.Connection) -> int:
    """对所有未筛选的 posts 跑预筛，返回标记为 signal 的数量。"""
    rows = conn.execute(
        "SELECT id, content, floor FROM posts WHERE is_signal = 0"
    ).fetchall()

    updated = 0
    for row in rows:
        # 首楼（floor=1）通常是楼主发的帖子主体，直接标记——内鬼帖的核心爆料都在首楼
        if row["floor"] == 1:
            conn.execute(
                "UPDATE posts SET is_signal=1, signal_reason=? WHERE id=?",
                ("首楼", row["id"]),
            )
            updated += 1
            continue
        is_sig, reason = _check(row["content"] or "")
        if is_sig:
            conn.execute(
                "UPDATE posts SET is_signal=1, signal_reason=? WHERE id=?",
                (reason, row["id"]),
            )
            updated += 1

    conn.commit()
    return updated
