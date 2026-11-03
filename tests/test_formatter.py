from types import SimpleNamespace

from telethon.extensions import markdown
from telethon.tl.types import MessageEntityBlockquote, MessageEntityMention, MessageEntityUnderline

from tg2max.formatter import entities_to_elements, tg_to_max


def u16(s):
    return len(s.encode("utf-16-le")) // 2


def test_emoji_offsets_are_utf16_and_kept():
    text, ents = markdown.parse("🎃🤪 **bold** [link](https://example.com)")
    els = entities_to_elements(ents)
    assert els[0] == {"type": "STRONG", "from": u16("🎃🤪 "), "length": 4}
    assert els[1] == {"type": "LINK", "from": u16("🎃🤪 bold "), "length": 4,
                      "attributes": {"url": "https://example.com"}}
    # the element really points at the right substring in UTF-16 units
    b = text.encode("utf-16-le")
    assert b[els[0]["from"] * 2:(els[0]["from"] + els[0]["length"]) * 2].decode("utf-16-le") == "bold"


def test_all_supported_types_and_unsupported_dropped():
    text, ents = markdown.parse("__i__ ~~s~~ `c` ```\np\n```")
    ents += [MessageEntityUnderline(0, 1), MessageEntityBlockquote(0, 1), MessageEntityMention(0, 1)]
    types = [e["type"] for e in entities_to_elements(ents)]
    assert types == ["EMPHASIZED", "STRIKETHROUGH", "MONOSPACED", "CODE", "UNDERLINE", "QUOTE"]


def test_tg_to_max_uses_raw_text():
    msg = SimpleNamespace(message="plain *not markdown*", entities=None, text="rendered")
    assert tg_to_max(msg) == ("plain *not markdown*", [])
    assert tg_to_max(SimpleNamespace(message=None, entities=None)) == ("", [])
