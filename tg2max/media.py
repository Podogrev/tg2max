"""Download media from Telegram, yield temp path, clean up after use."""

import os
import tempfile
import logging
from contextlib import asynccontextmanager
from typing import Optional

from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
)

logger = logging.getLogger(__name__)


def detect_media_type(message) -> Optional[str]:
    """Determine the media type of a Telethon message."""
    if not message.media:
        return None
    if isinstance(message.media, MessageMediaPhoto):
        return "photo"
    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc is None:
            return None
        for attr in doc.attributes:
            attr_name = type(attr).__name__
            if attr_name == "DocumentAttributeVideo":
                return "video"
            if attr_name == "DocumentAttributeAudio":
                return "audio"
            if attr_name == "DocumentAttributeAnimated":
                return "animation"
        return "file"
    return None


@asynccontextmanager
async def download_temp(tg_client, message):
    """Download media to a temp file, yield the path, then delete it.

    Ensures minimal disk usage — only one file at a time.
    """
    suffix = ""
    media_type = detect_media_type(message)
    if media_type == "photo":
        suffix = ".jpg"
    elif media_type == "video":
        suffix = ".mp4"

    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        path = await tg_client.download_media(message, file=tmp_path)
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            logger.debug(
                "Downloaded %s for msg %d (%.1f KB)",
                media_type,
                message.id,
                os.path.getsize(path) / 1024,
            )
            yield path
        else:
            logger.warning("Failed to download media for message %d", message.id)
            yield None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
