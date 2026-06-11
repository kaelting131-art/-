"""关键词预筛。对 posts 表里 is_signal=0 的新帖跑一遍，命中则标记 is_signal=1。
这一步只做粗筛，目的是把 LLM 调用量压到 5-10%。
触发词按吧的 topic 区分：leak=爆料/内鬼，strength=强度分析。
"""
import sqlite3

# 爆料吧触发词：出现即值得关注，宁可错杀
LEAK_TRIGGER_WORDS = [
    "爆料", "内鬼", "前瞻", "下个版本", "下版本",
    "新角色", "新武器", "up池", "卡池", "数值",
    "技能", "立绘", "5星", "五星", "限定", "复刻",
    "泄露", "流出", "内部", "测试服", "beta", "leak",
    "前瞻", "版本号", "未公开", "提前",
]

# 强度吧触发词：强度结论、配队、抽卡建议
STRENGTH_TRIGGER_WORDS = [
    "强度", "t0", "t1", "t2", "节奏榜", "毕业",
    "配队", "组队", "阵容", "适配", "练度",
    "抽不抽", "值得抽", "值得练", "必抽", "建议抽",
    "倍率", "天花板", "上限", "超标", "超模",
    "伤害", "dps", "深塔", "满命", "0命", "零命",
    "专武", "对比", "强不强", "数值",
]

TRIGGER_WORDS_BY_TOPIC = {
    "leak": LEAK_TRIGGER_WORDS,
    "strength": STRENGTH_TRIGGER_WORDS,
}

# 反向词：出现则降权（即使命中触发词也取消标记）
NOISE_WORDS = [
    "钓鱼", "营销号", "月经贴", "引战", "辟谣",
    "是真的吗", "有图吗", "水贴", "转载", "搬运",
    "已删", "求证",
]


def _check(text: str, topic: str) -> tuple[bool, str]:
    """返回 (is_signal, 命中的触发词逗号分隔)"""
    if not text:
        return False, ""
    low = text.lower()
    triggers = TRIGGER_WORDS_BY_TOPIC.get(topic, LEAK_TRIGGER_WORDS)
    hits = [w for w in triggers if w.lower() in low]
    if not hits:
        return False, ""
    noise = [w for w in NOISE_WORDS if w.lower() in low]
    if noise:
        return False, ""
    return True, ",".join(hits)


def run(conn: sqlite3.Connection) -> int:
    """对所有未筛选的 posts 跑预筛，返回标记为 signal 的数量。"""
    rows = conn.execute("""
        SELECT p.id, p.content, p.floor, COALESCE(f.topic, 'leak') AS topic
        FROM posts p
        JOIN threads t ON t.tid = p.tid
        LEFT JOIN forums f ON f.kw = t.forum_kw
        WHERE p.is_signal = 0
    """).fetchall()

    updated = 0
    for row in rows:
        # 首楼（floor=1）通常是楼主发的帖子主体，直接标记——核心内容都在首楼
        if row["floor"] == 1:
            conn.execute(
                "UPDATE posts SET is_signal=1, signal_reason=? WHERE id=?",
                ("首楼", row["id"]),
            )
            updated += 1
            continue
        is_sig, reason = _check(row["content"] or "", row["topic"])
        if is_sig:
            conn.execute(
                "UPDATE posts SET is_signal=1, signal_reason=? WHERE id=?",
                (reason, row["id"]),
            )
            updated += 1

    conn.commit()
    return updated
