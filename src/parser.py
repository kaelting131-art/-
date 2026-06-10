"""贴吧 HTML 解析。只用正则/字符串处理，避免引入 bs4 依赖。
贴吧前端 DOM 改动频繁，解析器必然脆弱——所有失败都降级为返回 None/[]，让快照留底。
"""
import hashlib
import re
from html import unescape

_RE_THREAD_ID = re.compile(r'/p/(\d+)')
_RE_TITLE = re.compile(r'title="([^"]+)"\s+href="/p/\d+"', re.S)
_RE_REPLY_COUNT = re.compile(r'class="threadlist_rep_num[^"]*"[^>]*>(\d+)<', re.S)
_RE_AUTHOR_NAME = re.compile(r'data-field=\'(\{[^\']*?"user_name"[^\']*?\})\'', re.S)
_RE_AUTHOR_NAME_HTML = re.compile(r'data-field="(\{[^"]*?&quot;user_name&quot;[^"]*?\})"', re.S)

_RE_POST_BLOCK = re.compile(
    r'<div[^>]*class="l_post[^"]*"[^>]*data-field=\'(\{.*?\})\'.*?'
    r'<cc>(.*?)</cc>',
    re.S,
)
_RE_TAG = re.compile(r'<[^>]+>')


def _strip_html(html: str) -> str:
    text = _RE_TAG.sub("", html)
    return unescape(text).strip()


def parse_list_page(html: str) -> list[dict]:
    """从吧的列表页提取帖子摘要列表。返回 [{tid, title, reply_count, author_name, author_id}]"""
    threads = {}
    # 用 href="/p/<tid>" 块定位每一个帖子
    for m in re.finditer(
        r'href="/p/(\d+)"[^>]*title="([^"]+)"',
        html,
    ):
        tid = int(m.group(1))
        if tid in threads:
            continue
        threads[tid] = {
            "tid": tid,
            "title": unescape(m.group(2)),
            "reply_count": None,
            "author_name": None,
            "author_id": None,
        }
    return list(threads.values())


def parse_thread_page(html: str) -> list[dict]:
    """从帖子详情页提取楼层。返回 [{floor, author_name, author_id, content, content_hash, posted_at}]"""
    posts = []
    import json as _json
    for m in _RE_POST_BLOCK.finditer(html):
        try:
            field = _json.loads(unescape(m.group(1)))
        except Exception:
            continue
        author = field.get("author", {})
        content_html = m.group(2)
        content = _strip_html(content_html)
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
