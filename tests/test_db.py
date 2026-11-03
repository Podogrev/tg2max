import asyncio

from tg2max import db


def test_mark_and_meta(tmp_db):
    async def go():
        assert await db.get_last_migrated_id() is None
        await db.mark_migrated(5, "a")
        await db.mark_migrated([7, 6], "b")
        await db.mark_migrated(5, "dup")  # ignored
        await db.set_meta_int("k", 42)
        return (await db.is_migrated(6), await db.is_migrated(8),
                await db.get_last_migrated_id(), await db.get_meta_int("k"))
    assert asyncio.run(go()) == (True, False, 7, 42)
