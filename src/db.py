import sqlite3
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS forums (
    kw          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    game        TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT 'leak',  -- leak=爆料/内鬼，strength=强度分析
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
    is_op         INTEGER NOT NULL DEFAULT 0,   -- 1=楼主本人的楼层
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
    if "is_op" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN is_op INTEGER NOT NULL DEFAULT 0")
    # 新列加完后才能建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_signal ON posts(is_signal)")
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
    if "read_at" not in tcols:
        conn.execute("ALTER TABLE threads ADD COLUMN read_at TEXT")  # 仪表盘已读标记
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(forums)")}
    if "topic" not in fcols:
        conn.execute("ALTER TABLE forums ADD COLUMN topic TEXT NOT NULL DEFAULT 'leak'")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
    if "content_hash" not in scols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN content_hash TEXT")
    if "raw_gz" not in scols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN raw_gz BLOB")
    conn.commit()


def snapshot_html(row) -> str:
    """从快照行还原 HTML。新行存 zlib 压缩的 raw_gz，旧行存明文 raw_html。"""
    if row["raw_gz"] is not None:
        return zlib.decompress(row["raw_gz"]).decode("utf-8", "replace")
    return row["raw_html"]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn
