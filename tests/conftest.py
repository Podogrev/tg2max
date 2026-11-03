import os

# tg2max.config requires these at import time; tests never talk to real services.
for k, v in {"TG_API_ID": "1", "TG_API_HASH": "x", "TG_PHONE": "+10000000000",
             "MAX_CHAT_ID": "-1", "POST_DELAY": "0"}.items():
    os.environ.setdefault(k, v)

import asyncio  # noqa: E402

import pytest  # noqa: E402

from tg2max import db  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TG2MAX_DB", str(tmp_path / "test.db"))
    yield
    asyncio.run(db.close_db())
