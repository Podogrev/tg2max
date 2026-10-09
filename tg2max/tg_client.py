import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from telethon import TelegramClient, events

from tg2max.config import TG_API_ID, TG_API_HASH, TG_PHONE, TG_CHANNEL
from tg2max import db
from tg2max.max_client import MaxClient
from tg2max.media import detect_media_type, download_temp
from tg2max.formatter import tg_entities_to_markdown

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


async def _process_message(
    tg_client: TelegramClient,
    max_client: MaxClient,
    message,
) -> None:
    """Process a single TG message: transfer to MAX."""
    text = tg_entities_to_markdown(message)
    has_media = detect_media_type(message) is not None

    if not text and not has_media:
        logger.debug("Skipping empty message %d", message.id)
        return

    if has_media:
        async with download_temp(tg_client, message) as tmp_path:
            if tmp_path:
                await max_client.send_media(tmp_path, caption=text)
            elif text:
                await max_client.send_text(text)
    elif text:
        await max_client.send_text(text)

    await db.mark_migrated(message.id)
    logger.info("Migrated TG msg %d → MAX", message.id)


async def _process_album(
    tg_client: TelegramClient,
    max_client: MaxClient,
    messages: list,
) -> None:
    """Process an album — download all media, send as group, clean up."""
    # Get caption from first message that has text
    caption = ""
    for msg in messages:
        t = tg_entities_to_markdown(msg)
        if t:
            caption = t
            break

    # Download all album media to temp files
    tmp_paths = []
    try:
        for msg in messages:
            if detect_media_type(msg) is None:
                continue
            suffix = ".jpg" if detect_media_type(msg) == "photo" else ".mp4"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            path = await tg_client.download_media(msg, file=tmp_path)
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                tmp_paths.append(path)
            else:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        if tmp_paths:
            await max_client.send_media_group(tmp_paths, caption=caption)
        elif caption:
            await max_client.send_text(caption)
    finally:
        # Clean up all temp files
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    for msg in messages:
        await db.mark_migrated(msg.id)
    logger.info("Migrated album (%d messages) → MAX", len(messages))


async def migrate_all(tg_client: TelegramClient, max_client: MaxClient, since_date: Optional[datetime] = None) -> None:
    """Migrate all historical posts from TG channel to MAX, preserving order."""
    await db.init_db()

    album_buffer: Dict[int, list] = {}
    processed_groups: Set[int] = set()

    # Collect all messages
    all_messages = []
    async for message in tg_client.iter_messages(TG_CHANNEL, reverse=True):
        if since_date and message.date and message.date < since_date:
            continue
        all_messages.append(message)

    logger.info("Found %d messages in TG channel", len(all_messages))

    # Group album messages
    for msg in all_messages:
        if msg.grouped_id:
            album_buffer.setdefault(msg.grouped_id, []).append(msg)

    # Count unique posts (albums count as 1)
    total_posts = 0
    seen_groups = set()
    for msg in all_messages:
        if msg.grouped_id:
            if msg.grouped_id not in seen_groups:
                seen_groups.add(msg.grouped_id)
                total_posts += 1
        else:
            total_posts += 1

    # Process in chronological order
    count = 0
    skipped = 0
    for message in all_messages:
        # Albums count as one post: only the first message of a group is considered
        if message.grouped_id:
            if message.grouped_id in processed_groups:
                continue
            processed_groups.add(message.grouped_id)

        if await db.is_migrated(message.id):
            skipped += 1
            continue

        if message.grouped_id:
            try:
                await _process_album(tg_client, max_client, album_buffer[message.grouped_id])
            except Exception as e:
                logger.error("Failed to migrate album (msg %d): %s", message.id, e)
                continue
        else:
            try:
                await _process_message(tg_client, max_client, message)
            except Exception as e:
                logger.error("Failed to migrate msg %d: %s", message.id, e)
                continue

        count += 1
        if count % 10 == 0:
            logger.info("Progress: %d/%d posts migrated (%d skipped)", count, total_posts, skipped)

        # Rate limiting
        await asyncio.sleep(2)

    logger.info("Migration complete: %d posts transferred, %d skipped", count, skipped)


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
