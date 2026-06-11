"""贴吧 HTML 解析。只用正则/字符串处理，避免引入 bs4 依赖。
贴吧前端 DOM 改动频繁，解析器必然脆弱——所有失败都降级为返回 None/[]，让快照留底。

详情页结构（新版 Vue SPA）：
  主楼区  <div class="pb-content-wrap">  楼主的帖子主体（第1楼）
  回复区  <div class="pb-comment-item">  每个是一个回复楼，含 comment-desc-left 的「第N楼」
楼主识别：主楼作者即楼主，回复楼按 author_id 与主楼相同来判定 is_op。
"""
import hashlib
import re
from html import unescape

_RE_TAG = re.compile(r'<[^>]+>')

# 新版贴吧（Vue SPA）用完整 URL，帖子链接形如 https://tieba.baidu.com/p/<tid>
_RE_TID_URL = re.compile(r'https://tieba\.baidu\.com/p/(\d+)')

_RE_HEAD_NAME = re.compile(r'class="head-name"[^>]*>(.*?)</a>', re.S)
# head-name 的 <a> 自带 href=home/main?id=楼主id，一次拿到 (作者id, 作者名)，保证两者对应
# （不能只抓块里第一个 home/main?id=——那往往是头像/推荐位的固定 UI 元素，不是发言者）
_RE_HEAD_LINK = re.compile(
    r'<a[^>]*?home/main\?id=(tb\.[0-9a-zA-Z._-]+)[^>]*?class="head-name"[^>]*?>(.*?)</a>',
    re.S,
)
_RE_AUTHOR_ID = re.compile(r'home/main\?id=(tb\.[0-9a-zA-Z._-]+)')
_RE_FLOOR = re.compile(r'class="comment-desc-left">.*?<span[^>]*>\s*第?\s*(\d+)\s*楼', re.S)
_RE_DATE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2})')

_RE_POST_BLOCK = re.compile(
    r'<div[^>]*class="l_post[^"]*"[^>]*data-field=\'(\{.*?\})\'.*?'
    r'<cc>(.*?)</cc>',
    re.S,
)


def _strip_html(html: str) -> str:
    text = _RE_TAG.sub("", html)
    return unescape(text).strip()


def _clean_text(frag: str) -> str:
    """去掉 svg/style 等非正文标签后取纯文本，压缩空白。"""
    frag = re.sub(r'<svg.*?</svg>', ' ', frag, flags=re.S)
    frag = re.sub(r'<style.*?</style>', ' ', frag, flags=re.S)
    txt = unescape(_RE_TAG.sub(" ", frag))
    return re.sub(r'\s+', " ", txt).strip()


def parse_list_page(html: str) -> list[dict]:
    """从吧的列表页提取帖子摘要列表。返回 [{tid, title, reply_count, author_name, author_id}]"""
    threads: dict[int, dict] = {}

    # 策略1：新版 Vue SPA
    # 以 class="thread-title" 为锚点，往前 5000 字节找最近的 /p/<tid> 和 head-name
    for m in re.finditer(r'class="thread-title"', html):
        idx = m.start()
        before = html[max(0, idx - 5000):idx]
        tid_matches = re.findall(r'tieba\.baidu\.com/p/(\d+)', before)
        if not tid_matches:
            continue
        tid = int(tid_matches[-1])
        if tid in threads:
            continue
        # 标题
        after = html[idx:idx + 600]
        spans = re.findall(r'<span[^>]*>([^<\s][^<]{3,})</span>', after)
        title = unescape(spans[0]).strip() if spans else None
        # 作者名：取 thread-title 前最后一个 head-name
        all_names = re.findall(r'class="head-name"[^>]*>\s*([^<]+?)\s*</a>', before)
        author_name = unescape(all_names[-1]).strip() if all_names else None
        threads[tid] = {"tid": tid, "title": title,
                        "reply_count": None, "author_name": author_name, "author_id": None}

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


def _mk_post(floor, author_name, author_id, content, is_op, posted_at):
    content = (content or "")[:2000]
    return {
        "floor": floor,
        "author_name": author_name,
        "author_id": author_id,
        "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        "posted_at": posted_at,
        "is_op": is_op,
    }


def _parse_main_floor(head: str) -> dict | None:
    """主楼（第1楼）：pb-content-wrap 区的正文 + 楼主信息。head 是第一个回复楼之前的 HTML。"""
    cw = re.search(
        r'class="pb-content-wrap">(.*?)'
        r'(?=<div[^>]*class="pc-pb-first-floor-interactive"|<div[^>]*class="pb-comment-list"'
        r'|<div[^>]*class="pc-pb-reply-area")',
        head, re.S,
    )
    if not cw:
        return None
    content = _clean_text(cw.group(1))
    title_m = re.search(r'class="pb-title"[^>]*>(.*?)</span>', head, re.S)
    title = _clean_text(title_m.group(1)) if title_m else ""
    # 标题并入正文头部——标题常含关键爆料/结论
    full = " ".join(x for x in (title, content) if x).strip()
    # 楼主名/ID：主楼区最后一个 head-name 链接（最接近正文的那个就是楼主）
    links = _RE_HEAD_LINK.findall(head)
    author_id = links[-1][0] if links else None
    author_name = _clean_text(links[-1][1]) if links else None
    date_m = _RE_DATE.search(re.sub(r'<[^>]+>', ' ', head[-1500:])) if head else None
    if not full and not author_id:
        return None
    return _mk_post(1, author_name, author_id, full, 1, date_m.group(1) if date_m else None)


def _parse_reply_floor(chunk: str, op_id: str | None) -> dict | None:
    """回复楼：pb-comment-item 块。op_id 用于判定该楼是否楼主自己盖的楼。"""
    lm = _RE_HEAD_LINK.search(chunk)
    author_id = lm.group(1) if lm else None
    author_name = _clean_text(lm.group(2)) if lm else None
    floor_m = _RE_FLOOR.search(chunk)
    floor = int(floor_m.group(1)) if floor_m else None
    body_m = re.search(
        r'class="pb-rich-text">(.*?)(?=<div[^>]*class="pc-pb-comments-desc")',
        chunk, re.S,
    )
    content = _clean_text(body_m.group(1)) if body_m else ""
    if floor is None and not content:
        return None
    # 楼层日期：comment-desc-left 里第N楼后面的那个日期 span
    desc_m = re.search(r'class="comment-desc-left">(.*?)</div>', chunk, re.S)
    posted_at = None
    if desc_m:
        dm = _RE_DATE.search(_clean_text(desc_m.group(1)))
        posted_at = dm.group(1) if dm else None
    is_op = 1 if (op_id and author_id == op_id) else 0
    return _mk_post(floor, author_name, author_id, content, is_op, posted_at)


def parse_thread_page(html: str) -> list[dict]:
    """从帖子详情页提取楼层。返回 [{floor, author_name, author_id, content, content_hash, posted_at, is_op}]"""
    posts: list[dict] = []

    # 按 pb-comment-item 切分：chunks[0] 含主楼区，后续每个是一个回复楼
    chunks = re.split(r'(?=<div[^>]*class="pb-comment-item")', html)
    head = chunks[0]

    main = _parse_main_floor(head)
    op_id = main["author_id"] if main else None
    if main:
        posts.append(main)

    next_floor = 2
    for chunk in chunks[1:]:
        p = _parse_reply_floor(chunk, op_id)
        if not p:
            continue
        if p["floor"] is None:  # 楼层号没解析出来，按出现顺序兜底
            p["floor"] = next_floor
        next_floor = max(next_floor, p["floor"]) + 1
        posts.append(p)

    if posts:
        return posts

    # 降级：旧版 l_post + data-field + <cc>
    import json as _json
    for m in _RE_POST_BLOCK.finditer(html):
        try:
            field = _json.loads(unescape(m.group(1)))
        except Exception:
            continue
        author = field.get("author", {})
        content = _strip_html(m.group(2))
        posts.append(_mk_post(
            field.get("content", {}).get("post_no") or field.get("post_no"),
            author.get("user_name"),
            str(author.get("user_id") or author.get("portrait") or ""),
            content, 0,
            field.get("content", {}).get("date"),
        ))

    return posts
