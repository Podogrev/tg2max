"""SQLite tracking of copied posts. One shared connection per process."""

import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS migrated_posts (
    tg_message_id INTEGER PRIMARY KEY,
    max_message_id TEXT,
    migrated_at TEXT NOT NULL
)
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_conn: Optional[aiosqlite.Connection] = None


def db_path() -> str:
    """TG2MAX_DB or tg2max.db; a writable temp copy if the directory is read-only."""
    name = os.getenv("TG2MAX_DB", "tg2max.db")
    original = os.path.abspath(name)
    if os.path.exists(original) and not os.access(os.path.dirname(original), os.W_OK):
        tmp_db = os.path.join(tempfile.gettempdir(), os.path.basename(name))
        if not os.path.exists(tmp_db):
            shutil.copy2(original, tmp_db)
        return tmp_db
    return name


async def _db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(db_path())
        await _conn.execute(_CREATE_TABLE)
        await _conn.execute(_CREATE_META)
        await _conn.commit()
    return _conn


async def init_db() -> None:
    await _db()


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def _get_meta(key: str) -> Optional[str]:
    cur = await (await _db()).execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else None


async def _set_meta(key: str, value: str) -> None:
    db = await _db()
    await db.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await db.commit()


async def get_last_run_at() -> Optional[datetime]:
    v = await _get_meta("last_run_at")
    return datetime.fromisoformat(v) if v else None


async def set_last_run_at(ts: datetime) -> None:
    await _set_meta("last_run_at", ts.isoformat())


async def get_meta_int(key: str) -> Optional[int]:
    v = await _get_meta(key)
    return int(v) if v is not None else None


async def set_meta_int(key: str, value: int) -> None:
    await _set_meta(key, str(value))


async def is_migrated(tg_message_id: int) -> bool:
    cur = await (await _db()).execute(
        "SELECT 1 FROM migrated_posts WHERE tg_message_id = ?", (tg_message_id,)
    )
    return await cur.fetchone() is not None


async def mark_migrated(tg_message_ids, max_message_id: Optional[str] = None) -> None:
    """Mark one id or a list of ids (an album) as migrated, in one transaction."""
    ids = [tg_message_ids] if isinstance(tg_message_ids, int) else list(tg_message_ids)
    now = datetime.now(timezone.utc).isoformat()
    db = await _db()
    await db.executemany(
        "INSERT OR IGNORE INTO migrated_posts (tg_message_id, max_message_id, migrated_at) "
        "VALUES (?, ?, ?)",
        [(i, max_message_id, now) for i in ids],
    )
    await db.commit()


async def get_last_migrated_id() -> Optional[int]:
    cur = await (await _db()).execute("SELECT MAX(tg_message_id) FROM migrated_posts")
    row = await cur.fetchone()
    return row[0] if row and row[0] is not None else None
