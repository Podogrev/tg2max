"""MAX messenger client via Playwright browser automation."""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from tg2max.config import MAX_CHAT_ID

logger = logging.getLogger(__name__)

_ORIGINAL_STATE = "max_session.json"


def _get_state_file() -> str:
    """Use a writable copy if the original dir is not writable."""
    original = os.path.abspath(_ORIGINAL_STATE)
    if os.path.exists(original) and not os.access(os.path.dirname(original), os.W_OK):
        tmp_state = os.path.join(tempfile.gettempdir(), _ORIGINAL_STATE)
        shutil.copy2(original, tmp_state)
        return tmp_state
    return _ORIGINAL_STATE


STATE_FILE = _get_state_file()
MAX_WEB_URL = "https://web.max.ru"
CHAT_URL = f"{MAX_WEB_URL}/{MAX_CHAT_ID}"


class MaxClient:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self) -> None:
        """Launch browser and restore session if available."""
        self._playwright = await async_playwright().start()

        state_path = Path(STATE_FILE)
        context_opts = {
            "viewport": {"width": 1200, "height": 800},
        }
        if state_path.exists():
            context_opts["storage_state"] = STATE_FILE
            logger.info("Restoring saved MAX session")

        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(**context_opts)
        self._page = await self._context.new_page()

        await self._page.goto(CHAT_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(2)

        # Check if we're logged in (look for the chat input)
        composer = self._page.get_by_test_id("composer")
        try:
            await composer.wait_for(timeout=10000)
            logger.info("MAX session active, channel ready")
        except Exception:
            logger.warning(
                "Not logged in to MAX. Please run: python -m tg2max.login_max"
            )
            raise RuntimeError(
                "MAX session not found. Run 'python -m tg2max.login_max' to log in first."
            )

    async def _ensure_channel(self) -> None:
        """Navigate to the target channel if not already there."""
        if CHAT_URL not in self._page.url:
            await self._page.goto(CHAT_URL, wait_until="networkidle")
            await asyncio.sleep(1)

    async def save_session(self) -> None:
        """Save browser session for reuse."""
        if self._context:
            await self._context.storage_state(path=STATE_FILE)
            logger.info("MAX session saved to %s", STATE_FILE)

    async def send_text(self, text: str) -> None:
        """Send a text post to the MAX channel."""
        if not self._page:
            raise RuntimeError("MaxClient not started")

        await self._ensure_channel()

        textbox = self._page.get_by_test_id("composer").get_by_role("textbox")
        await textbox.click()
        await textbox.fill(text)

        send_btn = self._page.get_by_role("button", name="Send message")
        await send_btn.click()
        await asyncio.sleep(0.5)
        logger.debug("Sent text: %.80s...", text)

    async def _upload_files(self, file_paths: List[str]) -> None:
        """Upload files via the menu: 'Upload file' → 'Photo or video' → file chooser."""
        upload_btn = self._page.get_by_role("button", name="Upload file")
        await upload_btn.click()
        await asyncio.sleep(0.3)

        menu_item = self._page.get_by_role("menuitem", name="Photo or video")

        async with self._page.expect_file_chooser() as fc_info:
            await menu_item.click()

        file_chooser = await fc_info.value
        await file_chooser.set_files(file_paths)

        # Wait for upload — longer for videos/large files
        total_size = sum(Path(f).stat().st_size for f in file_paths)
        wait_time = max(3, total_size // (1024 * 1024) * 2)
        await asyncio.sleep(wait_time)

    async def send_media(self, file_path: str, caption: Optional[str] = None) -> None:
        """Send a media file (photo/video) with optional caption."""
        if not self._page:
            raise RuntimeError("MaxClient not started")

        await self._ensure_channel()

        # Type caption first if provided
        if caption:
            textbox = self._page.get_by_test_id("composer").get_by_role("textbox")
            await textbox.click()
            await textbox.fill(caption)

        # Upload the file
        await self._upload_files([file_path])

        # Send
        send_btn = self._page.get_by_role("button", name="Send message")
        await send_btn.click()
        await asyncio.sleep(1)
        logger.debug("Sent media: %s", file_path)

    async def send_media_group(self, file_paths: List[str], caption: Optional[str] = None) -> None:
        """Send multiple media files (album) with optional caption."""
        if not file_paths:
            return

        if not self._page:
            raise RuntimeError("MaxClient not started")

        await self._ensure_channel()

        if caption:
            textbox = self._page.get_by_test_id("composer").get_by_role("textbox")
            await textbox.click()
            await textbox.fill(caption)

        # Upload all files at once
        await self._upload_files(file_paths)

        send_btn = self._page.get_by_role("button", name="Send message")
        await send_btn.click()
        await asyncio.sleep(1)
        logger.debug("Sent album: %d files", len(file_paths))

    async def close(self) -> None:
        """Save session and close browser."""
        await self.save_session()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
