"""Minimal client for MAX's internal web protocol (the one web.max.ru uses).

Transport: WebSocket to wss://api.oneme.ru/websocket.
Frame: header struct ">BBHHI" = ver, cmd, seq, opcode, (flags << 24 | payload_len);
payload is msgpack, LZ4-block compressed when flags > 0 (server -> client only).
cmd: 0 request, 1 response, 2 event, 3 error.

Unofficial API: it may change without notice. Protocol reference: PyMax (MIT),
https://github.com/MaxApiTeam/PyMax
"""

import asyncio
import itertools
import mimetypes
import os
import ssl
import struct
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
import msgpack
import websockets

WS_URL = "wss://api.oneme.ru/websocket"
ORIGIN = "https://web.max.ru"
APP_VERSION = "26.10.11"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
# Upload hosts are signed by the Russian Trusted Root CA, absent from default trust stores.
CA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rootca_ssl_rsa2022.crt")

OP_PING = 1
OP_SESSION_INIT = 6
OP_LOGIN = 19
OP_MSG_SEND = 64
OP_PHOTO_UPLOAD = 80
OP_VIDEO_UPLOAD = 82
OP_LOGIN_CHECK_PASSWORD = 115
OP_NOTIF_ATTACH = 136
OP_GET_QR = 288
OP_GET_QR_STATUS = 289
OP_LOGIN_BY_QR = 291

CMD_REQUEST, CMD_RESPONSE, CMD_EVENT, CMD_ERROR = 0, 1, 2, 3
PROTO_VER = 10
_HDR = struct.Struct(">BBHHI")


class MaxError(Exception):
    pass


# --- framing -----------------------------------------------------------------

def _lz4_block_decompress(src: bytes) -> bytes:
    dst = bytearray()
    pos = 0
    while pos < len(src):
        token = src[pos]
        pos += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[pos]
                pos += 1
                lit += b
                if b != 255:
                    break
        dst += src[pos:pos + lit]
        pos += lit
        if pos >= len(src):
            break
        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        match = (token & 15) + 4
        if token & 15 == 15:
            while True:
                b = src[pos]
                pos += 1
                match += b
                if b != 255:
                    break
        start = len(dst) - offset
        for i in range(match):
            dst.append(dst[start + i])
    return bytes(dst)


def _ext_hook(code, data):
    if code == 1:  # wrapped msgpack value
        return msgpack.unpackb(data, raw=False, strict_map_key=False, ext_hook=_ext_hook)
    return msgpack.ExtType(code, data)


def unpack_frame(data: bytes) -> dict:
    ver, cmd, seq, opcode, packed = _HDR.unpack_from(data)
    flags, length = packed >> 24, packed & 0xFFFFFF
    body = data[_HDR.size:_HDR.size + length]
    if flags:
        body = _lz4_block_decompress(body)
    payload = msgpack.unpackb(body, raw=False, strict_map_key=False, ext_hook=_ext_hook) if body else None
    return {"cmd": cmd, "seq": seq, "opcode": opcode, "payload": payload}


def pack_frame(cmd: int, seq: int, opcode: int, payload) -> bytes:
    body = msgpack.packb(payload, use_bin_type=True) if payload is not None else b""
    return _HDR.pack(PROTO_VER, cmd, seq, opcode, len(body)) + body


# --- client ------------------------------------------------------------------

class MaxConnection:
    """One authenticated WebSocket session. Use as an async context manager."""

    def __init__(self, token: Optional[str], device_id: str):
        self.token = token
        self.device_id = device_id
        self.me: Optional[int] = None
        self._seq = itertools.count()
        self._pending: dict = {}
        self._video_waiters: dict = {}
        self._ws = None
        self._reader = None
        self._http: Optional[aiohttp.ClientSession] = None  # one HTTP/TLS pool for all uploads
        self._ssl = ssl.create_default_context()
        self._ssl.load_verify_locations(CA_FILE)

    async def __aenter__(self):
        await self.connect()
        if self.token:
            await self.login()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            WS_URL, origin=ORIGIN, user_agent_header=USER_AGENT, max_size=10 * 2**20
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=900, sock_read=60),
            connector=aiohttp.TCPConnector(ssl=self._ssl),
        )
        await self.call(OP_SESSION_INIT, {
            "userAgent": {
                "deviceType": "WEB", "locale": "ru", "deviceLocale": "ru",
                "osVersion": "macOS", "deviceName": "Chrome", "headerUserAgent": USER_AGENT,
                "appVersion": APP_VERSION, "screen": "800x1200 1.0x",
                "timezone": "Europe/Lisbon",
            },
            "deviceId": self.device_id,
        })

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._http:
            await self._http.close()
        if self._ws:
            await self._ws.close()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                f = unpack_frame(raw)
                if f["cmd"] in (CMD_RESPONSE, CMD_ERROR):
                    fut = self._pending.pop(f["seq"], None)
                    if fut and not fut.done():
                        fut.set_result(f)
                elif f["opcode"] == OP_NOTIF_ATTACH and isinstance(f["payload"], dict):
                    fut = self._video_waiters.get(f["payload"].get("videoId"))
                    if fut and not fut.done():
                        fut.set_result(f["payload"])
                elif f["cmd"] == CMD_REQUEST and f["opcode"] == OP_PING:
                    await self._ws.send(pack_frame(CMD_RESPONSE, f["seq"], OP_PING, None))
        except websockets.ConnectionClosed as e:
            err = MaxError(f"connection closed: {e}")
            for fut in list(self._pending.values()) + list(self._video_waiters.values()):
                if not fut.done():
                    fut.set_exception(err)

    async def call(self, opcode: int, payload, timeout: float = 30):
        seq = next(self._seq) & 0xFFFF
        fut = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        await self._ws.send(pack_frame(CMD_REQUEST, seq, opcode, payload))
        try:
            f = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(seq, None)
        if f["cmd"] == CMD_ERROR:
            raise MaxError(f"opcode {opcode}: {f['payload']}")
        return f["payload"]

    # --- auth ---

    async def login(self) -> None:
        resp = await self.call(OP_LOGIN, {
            "token": self.token, "interactive": True, "chatsCount": 0,
            "chatsSync": 0, "contactsSync": 0, "presenceSync": -1, "draftsSync": 0,
        })
        self.me = resp["profile"]["contact"]["id"]

    async def qr_login(
        self,
        show_qr: Callable[[str], Awaitable[None]],
        ask_password: Optional[Callable[[Optional[str]], Awaitable[str]]] = None,
    ) -> str:
        """Log in by QR scanned in the MAX phone app. Returns the new login token."""
        q = await self.call(OP_GET_QR, {})
        await show_qr(q["qrLink"])
        while time.time() * 1000 < q["expiresAt"]:
            st = await self.call(OP_GET_QR_STATUS, {"trackId": q["trackId"]})
            if st["status"].get("loginAvailable"):
                break
            await asyncio.sleep(q["pollingInterval"] / 1000)
        else:
            raise MaxError("QR code expired, run login again")

        resp = await self.call(OP_LOGIN_BY_QR, {"trackId": q["trackId"]})
        token = (resp.get("tokenAttrs", {}).get("LOGIN") or {}).get("token")
        if not token and resp.get("passwordChallenge"):
            if ask_password is None:
                raise MaxError("2FA password required")
            ch = resp["passwordChallenge"]
            password = await ask_password(ch.get("hint"))
            resp = await self.call(OP_LOGIN_CHECK_PASSWORD, {"trackId": ch["trackId"], "password": password})
            token = (resp.get("tokenAttrs", {}).get("LOGIN") or {}).get("token")
        if not token:
            raise MaxError(f"no login token in response (keys: {list(resp)})")
        self.token = token
        await self.login()
        return token

    # --- uploads & messages ---

    async def upload_photo(self, path: str) -> dict:
        resp = await self.call(OP_PHOTO_UPLOAD, {"count": 1, "type": 0, "uploaderType": 0, "profile": False})
        url = resp["url"]
        q = parse_qs(urlparse(url).query)
        photo_id = (q.get("photos") or q.get("photoIds") or [None])[0]
        form = aiohttp.FormData()
        ctype = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as fh:
            form.add_field("file", fh.read(), filename=os.path.basename(path), content_type=ctype)
        async with self._http.post(url, data=form) as r:
            if r.status != 200:
                raise MaxError(f"photo upload HTTP {r.status}: {(await r.text())[:200]}")
            photos = (await r.json(content_type=None))["photos"]
        entry = photos.get(photo_id) or next(iter(photos.values()))
        return {"_type": "PHOTO", "photoToken": entry["token"]}

    async def upload_video(self, path: str) -> dict:
        resp = await self.call(OP_VIDEO_UPLOAD, {"count": 1, "type": 0, "uploaderType": 0, "profile": False})
        info = resp["info"][0]
        video_id, token = info["videoId"], info["token"]
        ready = asyncio.get_running_loop().create_future()
        self._video_waiters[video_id] = ready  # register before POST: the event may beat the HTTP reply
        size = os.path.getsize(path)
        headers = {
            "Content-Disposition": f"attachment; filename={quote(os.path.basename(path))}",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Content-Type": "application/octet-stream",
        }
        try:
            with open(path, "rb") as fh:
                async with self._http.post(info["url"], data=fh, headers=headers) as r:
                    if r.status != 200:
                        raise MaxError(f"video upload HTTP {r.status}: {(await r.text())[:200]}")
            await asyncio.wait_for(ready, 180)
        finally:
            self._video_waiters.pop(video_id, None)
        return {"_type": "VIDEO", "videoId": video_id, "token": token, "videoType": 0}

    async def send_message(self, chat_id: int, text: str = "", elements=(), attaches=()) -> int:
        resp = await self.call(OP_MSG_SEND, {
            "chatId": chat_id,
            "message": {
                "text": text,
                "cid": int(time.time() * 1000),
                "elements": list(elements),
                "attaches": list(attaches),
            },
            "notify": True,
        })
        return resp["message"]["id"]
