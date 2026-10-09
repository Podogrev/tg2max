import asyncio
import logging
import sys
from datetime import datetime, timezone

from tg2max.config import TG_PHONE
from tg2max.tg_client import create_tg_client, migrate_all, watch
from tg2max.max_client import MaxClient
from tg2max import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run(mode: str, since_date=None, explicit_since: bool = False) -> None:
    await db.init_db()

    if mode in ("migrate", "sync") and not explicit_since:
        last = await db.get_last_run_at()
        if last:
            since_date = last
            logger.info("Resuming from last run: %s", last.isoformat())
        else:
            logger.info("No prior run recorded; migrating full history")

    run_started_at = datetime.now(timezone.utc)

    # Start MAX browser client
    max_client = MaxClient()
    await max_client.start()

    # Start Telegram client
    tg_client = create_tg_client()
    await tg_client.start(phone=TG_PHONE)
    logger.info("Telegram client connected")

    try:
        if mode == "migrate":
            await migrate_all(tg_client, max_client, since_date=since_date)
            await db.set_last_run_at(run_started_at)
        elif mode == "watch":
            await watch(tg_client, max_client)
        elif mode == "sync":
            await migrate_all(tg_client, max_client, since_date=since_date)
            await db.set_last_run_at(run_started_at)
            logger.info("Migration done, switching to watch mode...")
            await watch(tg_client, max_client)
        else:
            print(f"Unknown mode: {mode}")
            print("Usage: python -m tg2max [migrate|watch|sync]")
            sys.exit(1)
    finally:
        await max_client.close()
        await tg_client.disconnect()


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m tg2max [migrate|watch|sync] [--since YYYY-MM-DD]")
        print()
        print("  migrate  — one-time transfer of all posts from TG to MAX")
        print("  watch    — monitor TG channel and forward new posts to MAX")
        print("  sync     — migrate all, then switch to watch mode")
        print()
        print("  --since YYYY-MM-DD  — only migrate posts from this date onwards")
        print()
        print("First time? Run: python -m tg2max.login_max")
        sys.exit(1)

    mode = sys.argv[1]

    since_date = None
    explicit_since = False
    if "--since" in sys.argv:
        idx = sys.argv.index("--since")
        if idx + 1 < len(sys.argv):
            since_date = datetime.strptime(sys.argv[idx + 1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            explicit_since = True
            print(f"Migrating posts since {since_date.date()}")

    asyncio.run(run(mode, since_date=since_date, explicit_since=explicit_since))


if __name__ == "__main__":
    main()
