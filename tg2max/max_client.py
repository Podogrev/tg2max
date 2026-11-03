"""MAX client for the bot: posts text, photos, videos and albums to the MAX channel.

Talks to MAX's internal WebSocket protocol directly (see max_proto.py) — no browser.
Session (login token + device id) lives in max_token.json, created by `python -m tg2max.login_max`.
"""

import json
import logging
import os
from typing import List, Optional, Sequence

from tg2max.config import MAX_CHAT_ID
from tg2max.max_proto import MaxConnection, MaxError

logger = logging.getLogger(__name__)

SESSION_FILE = os.getenv("MAX_SESSION_FILE", "max_token.json")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".gif"}


class MaxSessionError(RuntimeError):
    pass


def load_session() -> dict:
    if not os.path.exists(SESSION_FILE):
        raise MaxSessionError(f"{SESSION_FILE} not found. Run: python -m tg2max.login_max")
    with open(SESSION_FILE) as f:
        return json.load(f)


def save_session(token: str, device_id: str, viewer_id: Optional[int]) -> None:
    fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token, "deviceId": device_id, "viewerId": viewer_id}, f)


class MaxClient:
    def __init__(self, chat_id: Optional[int] = None):
        self.chat_id = int(chat_id if chat_id is not None else MAX_CHAT_ID)
        self._conn: Optional[MaxConnection] = None

    async def start(self) -> None:
        s = load_session()
        self._conn = MaxConnection(s["token"], s["deviceId"])
        await self._conn.connect()
        try:
            await self._conn.login()
        except MaxError as e:
            await self._conn.close()
            raise MaxSessionError(f"MAX session rejected ({e}). Run: python -m tg2max.login_max") from e
        logger.info("MAX session active (user %s), target chat %s", self._conn.me, self.chat_id)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def _upload(self, path: str) -> dict:
        if os.path.splitext(path)[1].lower() in VIDEO_EXT:
            return await self._conn.upload_video(path)
        return await self._conn.upload_photo(path)

    async def send_post(
        self,
        text: str = "",
        elements: Sequence[dict] = (),
        file_paths: Sequence[str] = (),
    ) -> int:
        """Send one post: text with formatting and 0..N media files (N > 1 = album)."""
        attaches: List[dict] = []
        for p in file_paths:
            attaches.append(await self._upload(p))
        msg_id = await self._conn.send_message(self.chat_id, text, elements, attaches)
        logger.debug("Sent post %s: %d attachments", msg_id, len(attaches))
        return msg_id
