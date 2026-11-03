import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from telethon import TelegramClient, events

from tg2max import db
from tg2max.config import POST_DELAY, TG_API_HASH, TG_API_ID, TG_CHANNEL
from tg2max.formatter import tg_to_max
from tg2max.max_client import MaxClient
from tg2max.media import detect_media_type

logger = logging.getLogger(__name__)

SESSION_NAME = "tg_session"
_SUFFIX = {"photo": ".jpg", "video": ".mp4", "animation": ".mp4"}


def _session_path() -> str:
    """Use a writable copy of the session file if the original dir is not writable."""
    original = os.path.abspath(SESSION_NAME + ".session")
    if os.path.exists(original) and not os.access(os.path.dirname(original), os.W_OK):
        tmp_session = os.path.join(tempfile.gettempdir(), SESSION_NAME + ".session")
        shutil.copy2(original, tmp_session)
        logger.info("Copied TG session to %s (original dir not writable)", tmp_session)
        return os.path.join(tempfile.gettempdir(), SESSION_NAME)
    return SESSION_NAME


def create_tg_client() -> TelegramClient:
    return TelegramClient(_session_path(), TG_API_ID, TG_API_HASH)


class MigrationError(RuntimeError):
    pass


# --- one post: prepare (download, parallel) -> send (strictly sequential) -------------

@dataclass
class Prepared:
    messages: list
    text: str
    elements: list
    paths: List[str]
    tmp_dir: str

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


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
    raise RuntimeError(f"media download failed for TG msg {msg.id}")


async def prepare(tg_client: TelegramClient, messages: list) -> Prepared:
    """Collect text/formatting and download all media of a post. Album files download in
    parallel; the result keeps the album order."""
    text, elements = "", []
    for msg in messages:
        text, elements = tg_to_max(msg)
        if text:
            break
    tmp_dir = tempfile.mkdtemp(prefix="tg2max-")
    try:
        paths = await asyncio.gather(*(_download(tg_client, m, tmp_dir) for m in messages))
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return Prepared(messages, text, elements, [p for p in paths if p], tmp_dir)


async def send(max_client: MaxClient, post: Prepared) -> Optional[int]:
    """Send a prepared post and mark it migrated. Returns the MAX message id (None if empty)."""
    ids = [m.id for m in post.messages]
    if not post.text and not post.paths:
        logger.debug("Skipping empty post (msg %d)", ids[0])
        await db.mark_migrated(ids, None)
        return None
    max_id = await max_client.send_post(post.text, post.elements, post.paths)
    await db.mark_migrated(ids, str(max_id))
    logger.info("Migrated TG msg %s (%d media) -> MAX %s",
                ",".join(map(str, ids)), len(post.paths), max_id)
    return max_id


def group_posts(messages: list) -> List[list]:
    """Group messages into posts (albums by grouped_id), in chronological order."""
    posts: List[list] = []
    albums: Dict[int, list] = {}
    for m in messages:
        if m.grouped_id:
            if m.grouped_id in albums:
                albums[m.grouped_id].append(m)
                continue
            albums[m.grouped_id] = [m]
            posts.append(albums[m.grouped_id])
        else:
            posts.append([m])
    return posts


# --- migrate --------------------------------------------------------------------------

async def migrate_all(
    tg_client: TelegramClient,
    max_client: MaxClient,
    since_date: Optional[datetime] = None,
    min_id: int = 0,
) -> int:
    """Migrate posts newer than min_id (and not older than since_date) from TG to MAX.

    Order guarantee: posts are SENT strictly one after another, oldest first. Only the
    download of the next post overlaps with sending the current one. On the first failure
    the run stops, so a later post never overtakes a failed one. Returns the highest TG
    message id up to which everything is migrated. Raises MigrationError(msg, resume_id).
    """
    await db.init_db()

    # reverse=True + min_id: only messages after min_id, oldest first (no full-history scan).
    messages = [
        m async for m in tg_client.iter_messages(
            TG_CHANNEL, reverse=True, min_id=min_id, offset_date=since_date
        )
    ]
    all_posts = group_posts(messages)
    todo = []
    resume_id = min_id
    skipped = 0
    for post in all_posts:
        if await db.is_migrated(post[0].id):
            skipped += 1
            if not todo:  # only advance the resume point over a migrated prefix
                resume_id = max(resume_id, max(m.id for m in post))
            continue
        todo.append(post)
    logger.info("Found %d messages, %d posts to send (after id %d, %d already migrated)",
                len(messages), len(todo), min_id, skipped)

    count = 0
    next_task: Optional[asyncio.Task] = None
    if todo:
        next_task = asyncio.create_task(prepare(tg_client, todo[0]))
    try:
        for i, post in enumerate(todo):
            task, next_task = next_task, None
            try:
                prepared = await task
            except Exception as e:
                raise MigrationError(f"TG msg {post[0].id}: {e}", resume_id) from e
            # Prefetch the next post while this one uploads and sends.
            if i + 1 < len(todo):
                next_task = asyncio.create_task(prepare(tg_client, todo[i + 1]))
            try:
                await send(max_client, prepared)
            except Exception as e:
                raise MigrationError(f"TG msg {post[0].id}: {e}", resume_id) from e
            finally:
                prepared.cleanup()

            resume_id = max(resume_id, max(m.id for m in post))
            count += 1
            if count % 10 == 0:
                logger.info("Progress: %d/%d posts migrated", count, len(todo))
            if i + 1 < len(todo):
                await asyncio.sleep(POST_DELAY)
    except MigrationError as e:
        logger.error("Failed to migrate %s. Stopping to keep order; it will be retried next run.",
                     e.args[0])
        raise
    finally:
        if next_task is not None:
            next_task.cancel()
            try:
                (await next_task).cleanup()
            except BaseException:
                pass

    logger.info("Migration complete: %d posts transferred, %d skipped", count, skipped)
    return resume_id


# --- watch ----------------------------------------------------------------------------

@dataclass
class _Pending:
    messages: list
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    timer: Optional[asyncio.Task] = None


async def watch(tg_client: TelegramClient, max_client: MaxClient, album_wait: float = 3.0) -> None:
    """Forward new TG posts to MAX as they appear, in arrival order.

    Every new post is queued at its first message; one worker sends the queue in order.
    An album is queued as soon as its first part arrives and becomes ready after
    `album_wait` seconds without new parts, so a later post can't overtake it.
    """
    await db.init_db()
    queue: "asyncio.Queue[_Pending]" = asyncio.Queue()
    albums: Dict[int, _Pending] = {}

    async def close_album_later(gid: int, p: _Pending) -> None:
        await asyncio.sleep(album_wait)
        albums.pop(gid, None)
        p.ready.set()

    async def worker() -> None:
        while True:
            p = await queue.get()
            await p.ready.wait()
            msgs = sorted(p.messages, key=lambda m: m.id)
            try:
                if await db.is_migrated(msgs[0].id):
                    continue
                prepared = await prepare(tg_client, msgs)
                try:
                    await send(max_client, prepared)
                finally:
                    prepared.cleanup()
            except Exception:
                logger.exception("Failed to forward TG msg %d", p.messages[0].id)
            await asyncio.sleep(POST_DELAY)

    @tg_client.on(events.NewMessage(chats=TG_CHANNEL))
    async def handler(event):
        # No awaits before the post is queued: handlers start in update order, so the
        # queue keeps arrival order even when Telethon runs handlers concurrently.
        m = event.message
        gid = m.grouped_id
        if gid and gid in albums:
            p = albums[gid]
            p.messages.append(m)
            p.timer.cancel()
            p.timer = asyncio.create_task(close_album_later(gid, p))
            return
        p = _Pending([m])
        if gid:
            albums[gid] = p
            p.timer = asyncio.create_task(close_album_later(gid, p))
        else:
            p.ready.set()
        queue.put_nowait(p)

    worker_task = asyncio.create_task(worker())
    logger.info("Watching TG channel for new posts...")
    try:
        await tg_client.run_until_disconnected()
    finally:
        worker_task.cancel()
