"""Log in to MAX by QR code shown right in the terminal. No browser needed.

Usage: python -m tg2max.login_max
Scan the QR with the MAX phone app. The session is saved to max_token.json.
"""

import asyncio
import getpass
import sys
import uuid

import qrcode

from tg2max.max_client import SESSION_FILE, save_session
from tg2max.max_proto import MaxConnection


async def show_qr(link: str) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(link)
    qr.print_ascii(invert=True)
    print("\nScan the QR code with the MAX mobile app. Waiting for confirmation...", flush=True)


async def ask_password(hint) -> str:
    return getpass.getpass(f"MAX cloud password (hint: {hint}): ")


async def login() -> None:
    device_id = str(uuid.uuid4())
    async with MaxConnection(None, device_id) as conn:
        token = await conn.qr_login(show_qr, ask_password if sys.stdin.isatty() else None)
        save_session(token, device_id, conn.me)
    print(f"Done: logged in as {conn.me}, session saved to {SESSION_FILE}")


if __name__ == "__main__":
    asyncio.run(login())
