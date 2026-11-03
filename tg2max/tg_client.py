import asyncio
import logging
import os
import tempfile
from datetime import datetime
from typing import Dict, Optional, Set

from telethon import TelegramClient, events

from tg2max.config import TG_API_ID, TG_API_HASH, TG_CHANNEL
from tg2max import db
from tg2max.max_client import MaxClient
from tg2max.media import detect_media_type
from tg2max.formatter import tg_to_max

logger = logging.getLogger(__name__)


SESSION_NAME = "tg_session"


def _session_path() -> str:
    """Use a writable copy of the session file if the original dir is not writable."""
    import shutil

    original = os.path.abspath(SESSION_NAME + ".session")
    if os.path.exists(original) and not os.access(os.path.dirname(original), os.W_OK):
        tmp_session = os.path.join(tempfile.gettempdir(), SESSION_NAME + ".session")
        shutil.copy2(original, tmp_session)
        logger.info("Copied TG session to %s (original dir not writable)", tmp_session)
        return os.path.join(tempfile.gettempdir(), SESSION_NAME)
    return SESSION_NAME


def create_tg_client() -> TelegramClient:
    return TelegramClient(_session_path(), TG_API_ID, TG_API_HASH)


_SUFFIX = {"photo": ".jpg", "video": ".mp4", "animation": ".mp4"}


async def _download(tg_client: TelegramClient, msg, tmp_dir: str) -> Optional[str]:
    """Download photo/video of a message into tmp_dir. Other media types are skipped."""
    kind = detect_media_type(msg)
    if kind is None:
        return None
    if kind not in _SUFFIX:
        logger.warning("Msg %d: unsupported media type %s, sending text only", msg.id, kind)
        return None
    path = await tg_client.download_media(msg, file=os.path.join(tmp_dir, f"{msg.id}{_SUFFIX[kind]}"))
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    logger.warning("Msg %d: media download failed", msg.id)
    return None


async def _send(tg_client: TelegramClient, max_client: MaxClient, messages: list) -> None:
    """Send one TG post (single message or album) as one MAX post, then mark all as migrated."""
    text, elements = "", []
    for msg in messages:
        text, elements = tg_to_max(msg)
        if text:
            break

    with tempfile.TemporaryDirectory(prefix="tg2max-") as tmp_dir:
        paths = []
        for msg in messages:
            p = await _download(tg_client, msg, tmp_dir)
            if p:
                paths.append(p)
        if not text and not paths:
            logger.debug("Skipping empty post (msg %d)", messages[0].id)
            return
        max_id = await max_client.send_post(text, elements, paths)

    for msg in messages:
        await db.mark_migrated(msg.id, str(max_id))
    logger.info("Migrated TG msg %s (%d media) -> MAX %s",
                ",".join(str(m.id) for m in messages), len(paths), max_id)


async def _process_message(tg_client: TelegramClient, max_client: MaxClient, message) -> None:
    await _send(tg_client, max_client, [message])


async def _process_album(tg_client: TelegramClient, max_client: MaxClient, messages: list) -> None:
    await _send(tg_client, max_client, messages)


class MigrationError(RuntimeError):
    pass


async def migrate_all(
    tg_client: TelegramClient,
    max_client: MaxClient,
    since_date: Optional[datetime] = None,
    min_id: int = 0,
) -> int:
    """Migrate posts newer than min_id (and not older than since_date) from TG to MAX.

    Posts go strictly in chronological order. On the first failure the run stops,
    so a later post never overtakes a failed one in MAX. Returns the highest TG message id
    up to which everything is migrated (the resume point for the next run).
    Raises MigrationError after saving progress if a post failed.
    """
    await db.init_db()

    # reverse=True + min_id: Telegram returns only messages after min_id, oldest first —
    # no full-history scan. offset_date with reverse=True means "from this date on".
    all_messages = [
        m async for m in tg_client.iter_messages(
            TG_CHANNEL, reverse=True, min_id=min_id, offset_date=since_date
        )
    ]
    logger.info("Found %d messages in TG channel (after id %d)", len(all_messages), min_id)

    album_buffer: Dict[int, list] = {}
    for msg in all_messages:
        if msg.grouped_id:
            album_buffer.setdefault(msg.grouped_id, []).append(msg)
    total_posts = len({m.grouped_id or -m.id for m in all_messages})

    processed_groups: Set[int] = set()
    count = skipped = 0
    resume_id = min_id
    for message in all_messages:
        if message.grouped_id:
            if message.grouped_id in processed_groups:
                continue  # album counts as one post: handled with its first message
            processed_groups.add(message.grouped_id)
        post = album_buffer[message.grouped_id] if message.grouped_id else [message]
        last_id = max(m.id for m in post)

        if await db.is_migrated(message.id):
            skipped += 1
            resume_id = max(resume_id, last_id)
            continue

        try:
            await _send(tg_client, max_client, post)
        except Exception as e:
            logger.error("Failed to migrate TG msg %d: %s. Stopping to keep order; "
                         "it will be retried next run.", message.id, e)
            raise MigrationError(f"TG msg {message.id}: {e}", resume_id) from e

        resume_id = max(resume_id, last_id)
        count += 1
        if count % 10 == 0:
            logger.info("Progress: %d/%d posts migrated (%d skipped)", count, total_posts, skipped)
        await asyncio.sleep(2)  # rate limiting

    logger.info("Migration complete: %d posts transferred, %d skipped", count, skipped)
    return resume_id


async def watch(tg_client: TelegramClient, max_client: MaxClient) -> None:
    """Monitor TG channel for new posts and forward them to MAX."""
    await db.init_db()

    album_buffer: Dict[int, list] = {}
    album_timers: Dict[int, asyncio.TimerHandle] = {}

    async def flush_album(grouped_id: int) -> None:
        msgs = album_buffer.pop(grouped_id, [])
        album_timers.pop(grouped_id, None)
        if msgs:
            await _process_album(tg_client, max_client, msgs)

    @tg_client.on(events.NewMessage(chats=TG_CHANNEL))
    async def handler(event):
        message = event.message

        if await db.is_migrated(message.id):
            return

        if message.grouped_id:
            album_buffer.setdefault(message.grouped_id, []).append(message)
            timer = album_timers.get(message.grouped_id)
            if timer:
                timer.cancel()
            loop = asyncio.get_event_loop()
            album_timers[message.grouped_id] = loop.call_later(
                3.0,
                lambda gid=message.grouped_id: asyncio.ensure_future(flush_album(gid)),
            )
        else:
            await _process_message(tg_client, max_client, message)

    logger.info("Watching TG channel for new posts...")
    await tg_client.run_until_disconnected()
