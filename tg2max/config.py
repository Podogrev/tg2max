import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


TG_API_ID = int(_require("TG_API_ID"))
TG_API_HASH = _require("TG_API_HASH")
TG_PHONE = _require("TG_PHONE")
TG_CHANNEL = os.getenv("TG_CHANNEL", "")
MAX_CHAT_ID = _require("MAX_CHAT_ID")

# Try to parse TG_CHANNEL as int (numeric ID), otherwise keep as string (username)
try:
    TG_CHANNEL = int(TG_CHANNEL)
except ValueError:
    pass
