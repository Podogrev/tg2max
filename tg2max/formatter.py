"""Convert Telegram message entities to MAX message elements.

Both use UTF-16 code-unit offsets, so entities map 1:1 onto the raw text
(Telethon `message.message`, not the markdown-rendered `message.text`).
"""

from typing import List, Tuple

TG_TO_MAX = {
    "MessageEntityBold": "STRONG",
    "MessageEntityItalic": "EMPHASIZED",
    "MessageEntityUnderline": "UNDERLINE",
    "MessageEntityStrike": "STRIKETHROUGH",
    "MessageEntityCode": "MONOSPACED",
    "MessageEntityPre": "CODE",
    "MessageEntityBlockquote": "QUOTE",
    "MessageEntityTextUrl": "LINK",
}


def entities_to_elements(entities) -> List[dict]:
    elements = []
    for e in entities or []:
        kind = TG_TO_MAX.get(type(e).__name__)
        if not kind:
            continue  # plain URLs, mentions, hashtags: MAX auto-detects or keeps as text
        el = {"type": kind, "from": e.offset, "length": e.length}
        if kind == "LINK":
            el["attributes"] = {"url": e.url}
        elements.append(el)
    return elements


def tg_to_max(message) -> Tuple[str, List[dict]]:
    """Return (text, elements) for a Telethon message."""
    text = message.message or ""
    if not text:
        return "", []
    return text, entities_to_elements(message.entities)
