import asyncio
import logging
import sys
from datetime import datetime, timezone

from tg2max.config import TG_PHONE
from tg2max.tg_client import MigrationError, create_tg_client, migrate_all, watch
from tg2max.max_client import MaxClient
from tg2max import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


RESUME_KEY = "resume_after_tg_id"


async def run(mode: str, since_date=None, explicit_since: bool = False) -> None:
    await db.init_db()

    # Resume point: everything up to this TG id is migrated. Bootstrap from the DB.
    resume = await db.get_meta_int(RESUME_KEY)
    if resume is None:
        resume = await db.get_last_migrated_id() or 0
    min_id = 0 if explicit_since else resume
    if mode in ("migrate", "sync") and not explicit_since:
        logger.info("Resuming after TG msg %d", min_id)

    run_started_at = datetime.now(timezone.utc)

    max_client = MaxClient()
    await max_client.start()

    tg_client = create_tg_client()
    await tg_client.start(phone=TG_PHONE)
    logger.info("Telegram client connected")

    async def migrate() -> None:
        try:
            done = await migrate_all(tg_client, max_client, since_date=since_date, min_id=min_id)
        except MigrationError as e:
            await db.set_meta_int(RESUME_KEY, min(resume, e.args[1]))
            logger.error("Migration stopped: %s", e.args[0])
            raise SystemExit(1)
        await db.set_meta_int(RESUME_KEY, max(resume, done))
        await db.set_last_run_at(run_started_at)

    try:
        if mode == "migrate":
            await migrate()
        elif mode == "watch":
            await watch(tg_client, max_client)
        elif mode == "sync":
            await migrate()
            logger.info("Migration done, switching to watch mode...")
            await watch(tg_client, max_client)
        else:
            print(f"Unknown mode: {mode}")
            print("Usage: python -m tg2max [migrate|watch|sync]")
            sys.exit(1)
    finally:
        await max_client.close()
        await tg_client.disconnect()
        await db.close_db()


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
