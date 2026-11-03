"""Detect the media type of a Telegram message."""

import logging
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
