import os
import shutil
import tempfile
from typing import Optional

import aiosqlite
from datetime import datetime, timezone

_ORIGINAL_DB = "tg2max.db"


def _get_db_path() -> str:
    """Use a writable copy of the DB if the original dir is not writable."""
    original = os.path.abspath(_ORIGINAL_DB)
    if os.path.exists(original) and not os.access(os.path.dirname(original), os.W_OK):
        tmp_db = os.path.join(tempfile.gettempdir(), _ORIGINAL_DB)
        if not os.path.exists(tmp_db):
            shutil.copy2(original, tmp_db)
        return tmp_db
    return _ORIGINAL_DB


DB_PATH = _get_db_path()

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


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_META)
        await db.commit()


async def get_last_run_at() -> Optional[datetime]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM meta WHERE key = 'last_run_at'")
        row = await cur.fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0])


async def set_last_run_at(ts: datetime) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO meta (key, value) VALUES ('last_run_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (ts.isoformat(),),
        )
        await db.commit()


async def is_migrated(tg_message_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM migrated_posts WHERE tg_message_id = ?",
            (tg_message_id,),
        )
        return await cur.fetchone() is not None


async def mark_migrated(tg_message_id: int, max_message_id: Optional[str] = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO migrated_posts (tg_message_id, max_message_id, migrated_at) VALUES (?, ?, ?)",
            (tg_message_id, max_message_id, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_last_migrated_id() -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT MAX(tg_message_id) FROM migrated_posts"
        )
        row = await cur.fetchone()
        return row[0] if row and row[0] is not None else None
