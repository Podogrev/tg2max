"""Convert Telegram message entities to markdown text for MAX."""

from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityStrike,
    MessageEntityUnderline,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageEntityMention,
    MessageEntityHashtag,
)


def tg_entities_to_markdown(message) -> str:
    """Convert a Telethon message with entities to markdown string."""
    text = message.text or message.message or ""
    if not text:
        return ""

    entities = message.entities or []
    if not entities:
        return text

    # Sort entities by offset (reverse to apply from end so offsets stay valid)
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)

    # Work with list of chars for proper Unicode handling
    chars = list(text)

    for entity in sorted_entities:
        start = entity.offset
        end = entity.offset + entity.length
        fragment = text[start:end]

        replacement = None
        if isinstance(entity, MessageEntityBold):
            replacement = f"**{fragment}**"
        elif isinstance(entity, MessageEntityItalic):
            replacement = f"_{fragment}_"
        elif isinstance(entity, MessageEntityCode):
            replacement = f"`{fragment}`"
        elif isinstance(entity, MessageEntityPre):
            lang = getattr(entity, "language", "") or ""
            replacement = f"```{lang}\n{fragment}\n```"
        elif isinstance(entity, MessageEntityStrike):
            replacement = f"~~{fragment}~~"
        elif isinstance(entity, MessageEntityUnderline):
            # Markdown doesn't have underline, keep as-is
            replacement = fragment
        elif isinstance(entity, MessageEntityTextUrl):
            replacement = f"[{fragment}]({entity.url})"
        elif isinstance(entity, (MessageEntityUrl, MessageEntityMention, MessageEntityHashtag)):
            # Keep as-is
            replacement = fragment

        if replacement is not None and replacement != fragment:
            chars[start:end] = list(replacement)

    return "".join(chars)
