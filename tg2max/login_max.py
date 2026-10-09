"""Interactive login to MAX web client.

Run this once to scan the QR code and save the browser session.
After that, tg2max can use the saved session headlessly.

Usage: python -m tg2max.login_max
"""

import asyncio
import logging

from playwright.async_api import async_playwright

from tg2max.max_client import STATE_FILE, MAX_WEB_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def login():
    pw = await async_playwright().start()

    # Launch visible browser for QR code scanning
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(viewport={"width": 1200, "height": 800})
    page = await context.new_page()

    await page.goto(MAX_WEB_URL)

    print()
    print("=" * 50)
    print("Open the MAX app on your phone")
    print("and scan the QR code in the browser window.")
    print()
    print("After a successful login, press Enter here...")
    print("=" * 50)
    print()

    input(">>> Press Enter after logging in to MAX: ")

    # Save session
    await context.storage_state(path=STATE_FILE)
    logger.info("Session saved to %s", STATE_FILE)

    await browser.close()
    await pw.stop()
    print("Done! Now run: python -m tg2max migrate")


if __name__ == "__main__":
    asyncio.run(login())
