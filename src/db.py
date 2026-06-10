import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS forums (
    kw          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    game        TEXT NOT NULL,
    first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    tid              INTEGER PRIMARY KEY,
    forum_kw         TEXT NOT NULL,
    title            TEXT,
    author_name      TEXT,
    author_id        TEXT,
    reply_count      INTEGER,
    created_at       TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    deleted_at       TEXT,
    FOREIGN KEY (forum_kw) REFERENCES forums(kw)
);
CREATE INDEX IF NOT EXISTS idx_threads_forum ON threads(forum_kw);
CREATE INDEX IF NOT EXISTS idx_threads_last_seen ON threads(last_seen);

-- 每次抓到一个帖子页都存一份快照。删了也留得住，且能看演变。
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tid          INTEGER NOT NULL,
    page         INTEGER NOT NULL,
    captured_at  TEXT NOT NULL,
    raw_html     TEXT NOT NULL,
    FOREIGN KEY (tid) REFERENCES threads(tid)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_tid ON snapshots(tid, page, captured_at);

-- 解析后的楼层。同一楼层可能多次入库（编辑/补充）；用 (tid, floor, content_hash) 去重。
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tid           INTEGER NOT NULL,
    floor         INTEGER NOT NULL,
    author_name   TEXT,
    author_id     TEXT,
    content       TEXT,
    content_hash  TEXT NOT NULL,
    posted_at     TEXT,
    first_seen    TEXT NOT NULL,
    is_signal     INTEGER NOT NULL DEFAULT 0,  -- 1=命中关键词预筛
    signal_reason TEXT,                         -- 命中的关键词（逗号分隔）
    UNIQUE (tid, floor, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_posts_tid ON posts(tid, floor);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_id);

CREATE TABLE IF NOT EXISTS crawl_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    forum_kw     TEXT,
    threads_seen INTEGER DEFAULT 0,
    threads_new  INTEGER DEFAULT 0,
    posts_new    INTEGER DEFAULT 0,
    error        TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
    if "is_signal" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN is_signal INTEGER NOT NULL DEFAULT 0")
    if "signal_reason" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN signal_reason TEXT")
    # 新列加完后才能建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_signal ON posts(is_signal)")
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn
