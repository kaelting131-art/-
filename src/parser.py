"""贴吧 HTML 解析。只用正则/字符串处理，避免引入 bs4 依赖。
贴吧前端 DOM 改动频繁，解析器必然脆弱——所有失败都降级为返回 None/[]，让快照留底。
"""
import hashlib
import re
from html import unescape

_RE_TAG = re.compile(r'<[^>]+>')

# 新版贴吧（Vue SPA）用完整 URL，帖子链接形如 https://tieba.baidu.com/p/<tid>
_RE_TID_URL = re.compile(r'https://tieba\.baidu\.com/p/(\d+)')

_RE_POST_BLOCK = re.compile(
    r'<div[^>]*class="l_post[^"]*"[^>]*data-field=\'(\{.*?\})\'.*?'
    r'<cc>(.*?)</cc>',
    re.S,
)


def _strip_html(html: str) -> str:
    text = _RE_TAG.sub("", html)
    return unescape(text).strip()


def parse_list_page(html: str) -> list[dict]:
    """从吧的列表页提取帖子摘要列表。返回 [{tid, title, reply_count, author_name, author_id}]"""
    threads: dict[int, dict] = {}

    # 策略1：新版 Vue SPA
    # 以 class="thread-title" 为锚点，往前 5000 字节找最近的 /p/<tid>，往后提取第一个非空 span 文字
    for m in re.finditer(r'class="thread-title"', html):
        idx = m.start()
        before = html[max(0, idx - 5000):idx]
        tid_matches = re.findall(r'tieba\.baidu\.com/p/(\d+)', before)
        if not tid_matches:
            continue
        tid = int(tid_matches[-1])
        if tid in threads:
            continue
        after = html[idx:idx + 600]
        spans = re.findall(r'<span[^>]*>([^<\s][^<]{3,})</span>', after)
        title = unescape(spans[0]).strip() if spans else None
        threads[tid] = {"tid": tid, "title": title,
                        "reply_count": None, "author_name": None, "author_id": None}

    # 策略2：旧版 —— href="/p/<tid>" title="..."
    if not threads:
        for m in re.finditer(r'href="/p/(\d+)"[^>]*title="([^"]+)"', html):
            tid = int(m.group(1))
            if tid in threads:
                continue
            threads[tid] = {"tid": tid, "title": unescape(m.group(2)),
                            "reply_count": None, "author_name": None, "author_id": None}

    # 策略3：纯 tid 降级（至少保证帖子不漏，标题为 None）
    if not threads:
        for m in _RE_TID_URL.finditer(html):
            tid = int(m.group(1))
            if tid not in threads:
                threads[tid] = {"tid": tid, "title": None,
                                "reply_count": None, "author_name": None, "author_id": None}

    return list(threads.values())


def parse_thread_page(html: str) -> list[dict]:
    """从帖子详情页提取楼层。返回 [{floor, author_name, author_id, content, content_hash, posted_at}]"""
    posts = []

    # 新版 Vue SPA：每个楼层是 virtual-list-item，data-key 是 pid
    for item_m in re.finditer(
        r'<div[^>]*data-key="(\d+)"[^>]*class="virtual-list-item">(.*?)'
        r'(?=<div[^>]*data-key="\d+"[^>]*class="virtual-list-item">|$)',
        html, re.S
    ):
        pid = item_m.group(1)
        block = item_m.group(2)

        # 作者名
        name_m = re.search(r'class="head-name"[^>]*>\s*([^<]+?)\s*</a>', block)
        author_name = unescape(name_m.group(1)).strip() if name_m else None

        # 作者 portrait id（从 href 里取）
        portrait_m = re.search(r'href="https://tieba\.baidu\.com/home/main\?id=([^&"]+)', block)
        author_id = portrait_m.group(1) if portrait_m else pid

        # 正文（可能有多个 pb-text-wrapper）
        texts = re.findall(r'class="pb-text-wrapper[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>',
                           block, re.S)
        content = " ".join(_strip_html(t) for t in texts).strip()
        if not content:
            content = _strip_html(re.sub(r'<style[^>]*>.*?</style>', '', block, flags=re.S))[:500]

        # 楼层号
        floor_m = re.search(r'第(\d+)楼', block)
        floor = int(floor_m.group(1)) if floor_m else None

        # 日期
        date_m = re.search(r'(\d{2}-\d{2}|\d{4}-\d{2}-\d{2})', block)
        posted_at = date_m.group(1) if date_m else None

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        posts.append({
            "floor": floor,
            "author_name": author_name,
            "author_id": author_id,
            "content": content,
            "content_hash": content_hash,
            "posted_at": posted_at,
        })

    # 降级：旧版 l_post + data-field + <cc>
    if not posts:
        import json as _json
        for m in _RE_POST_BLOCK.finditer(html):
            try:
                field = _json.loads(unescape(m.group(1)))
            except Exception:
                continue
            author = field.get("author", {})
            content = _strip_html(m.group(2))
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            posts.append({
                "floor": field.get("content", {}).get("post_no") or field.get("post_no"),
                "author_name": author.get("user_name"),
                "author_id": str(author.get("user_id") or author.get("portrait") or ""),
                "content": content,
                "content_hash": content_hash,
                "posted_at": field.get("content", {}).get("date"),
            })

    return posts
