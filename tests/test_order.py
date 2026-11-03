"""Post order guarantees of migrate_all and watch, with fake Telegram and MAX clients."""
import asyncio
import os
import random

import pytest
from telethon.tl.types import MessageMediaPhoto

from tg2max import db
from tg2max.tg_client import MigrationError, migrate_all, watch


class Msg:
    def __init__(self, id, grouped_id=None, text="", media=True):
        self.id, self.grouped_id, self.message, self.entities = id, grouped_id, text, None
        self.media = MessageMediaPhoto() if media else None


class FakeTG:
    def __init__(self, messages, delays=None):
        self.messages, self.delays = messages, delays or {}
        self.handler = None

    async def iter_messages(self, chat, reverse, min_id, offset_date):
        for m in self.messages:
            if m.id > min_id:
                yield m

    async def download_media(self, msg, file):
        await asyncio.sleep(self.delays.get(msg.id, random.uniform(0, 0.02)))
        with open(file, "wb") as f:
            f.write(str(msg.id).encode())
        return file

    def on(self, _event):
        def deco(fn):
            self.handler = fn
            return fn
        return deco


class FakeMAX:
    def __init__(self, fail_on=None):
        self.sent, self.fail_on, self.n = [], fail_on, 0

    async def send_post(self, text, elements, paths):
        ids = [int(open(p).read()) for p in paths]  # files carry their TG id
        if self.fail_on in ids:
            raise RuntimeError("boom")
        await asyncio.sleep(random.uniform(0, 0.02))
        self.sent.append(ids)
        self.n += 1
        return 1000 + self.n


def run(coro):
    return asyncio.run(coro)


def channel():
    # 1, album(2,3,4), 5, album(6,7), 8
    return [Msg(1), Msg(2, 10), Msg(3, 10), Msg(4, 10), Msg(5), Msg(6, 20), Msg(7, 20), Msg(8)]


def test_migrate_keeps_order_even_when_downloads_finish_out_of_order(tmp_db):
    # later files download much faster than earlier ones
    tg = FakeTG(channel(), delays={1: 0.15, 2: 0.12, 3: 0.0, 4: 0.08, 5: 0.0, 6: 0.1, 7: 0.0, 8: 0.0})
    mx = FakeMAX()
    assert run(migrate_all(tg, mx)) == 8
    assert mx.sent == [[1], [2, 3, 4], [5], [6, 7], [8]]


def test_migrate_stops_on_failure_and_resumes(tmp_db):
    mx = FakeMAX(fail_on=5)
    with pytest.raises(MigrationError) as e:
        run(migrate_all(FakeTG(channel()), mx))
    assert e.value.args[1] == 4          # resume point: everything up to 4 is done
    assert mx.sent == [[1], [2, 3, 4]]   # nothing after the failed post was sent

    async def second():
        mx2 = FakeMAX()
        done = await migrate_all(FakeTG(channel()), mx2, min_id=4)
        return done, mx2.sent
    done, sent = run(second())
    assert done == 8 and sent == [[5], [6, 7], [8]]


def test_migrate_skips_already_migrated_without_resending(tmp_db):
    async def go():
        await db.mark_migrated([2, 3, 4], "x")
        mx = FakeMAX()
        done = await migrate_all(FakeTG(channel()), mx)
        return done, mx.sent
    done, sent = run(go())
    assert done == 8 and sent == [[1], [5], [6, 7], [8]]


def test_watch_sends_in_arrival_order_album_not_overtaken(tmp_db):
    async def go():
        tg, mx = FakeTG([]), FakeMAX()

        async def run_until_disconnected():
            ev = lambda m: type("E", (), {"message": m})()  # noqa: E731
            # album 10 starts, a single post arrives before the album's last part
            for m in [Msg(1), Msg(2, 10), Msg(3, 10), Msg(5), Msg(4, 10), Msg(6)]:
                await tg.handler(ev(m))
                await asyncio.sleep(0.01)
            for _ in range(100):
                if mx.n == 4:
                    break
                await asyncio.sleep(0.02)
        tg.run_until_disconnected = run_until_disconnected
        await watch(tg, mx, album_wait=0.1)
        return mx.sent
    assert run(go()) == [[1], [2, 3, 4], [5], [6]]


def test_temp_dirs_cleaned_up(tmp_db, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(MigrationError):
        run(migrate_all(FakeTG(channel()), FakeMAX(fail_on=6)))
    assert [p for p in os.listdir(tmp_path) if p.startswith("tg2max-")] == []
