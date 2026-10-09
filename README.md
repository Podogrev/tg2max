# tg2max

Copy posts from a Telegram channel to a [MAX](https://max.ru) channel: text, photos, videos and albums, in chronological order.

MAX has no public API for channel posting, so tg2max drives the MAX web client (web.max.ru) with headless Chromium via Playwright. Telegram is read through the regular user API (MTProto, via Telethon).

## Features

- One-time migration of the full channel history, or from a given date
- Watch mode: forwards new Telegram posts to MAX as they appear
- Albums are sent as one MAX message with a shared caption
- Text formatting (bold, italic, code, links) is converted to Markdown
- Already copied posts are tracked in a local SQLite DB, so reruns never duplicate
- Incremental runs: without `--since`, migration resumes from the previous run

## Requirements

- Python 3.9+
- Telegram API credentials from https://my.telegram.org/apps
- A MAX account with permission to post in the target channel

## Setup

```bash
git clone https://github.com/<you>/tg2max.git
cd tg2max
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # then fill in the values
```

Log in to MAX once. A browser window opens; scan the QR code with the MAX mobile app, then press Enter in the terminal:

```bash
python -m tg2max.login_max
```

The session is saved to `max_session.json`. On the first Telegram run you will be asked for a login code; the session is saved to `tg_session.session`.

## Usage

```bash
# Copy the whole channel history (first run), or everything since the last run
python -m tg2max migrate

# Copy posts from a given date onwards
python -m tg2max migrate --since 2026-01-01

# Forward new posts in real time
python -m tg2max watch

# Migrate, then keep watching
python -m tg2max sync
```

For a scheduled daily copy, run `python -m tg2max migrate` from cron or launchd.

## How it works

| Module | Role |
|---|---|
| `tg2max/tg_client.py` | Reads the Telegram channel, groups albums by `grouped_id`, drives migration and watch mode |
| `tg2max/max_client.py` | Posts to MAX through the web client (Playwright) |
| `tg2max/formatter.py` | Converts Telegram message entities to Markdown |
| `tg2max/media.py` | Detects media type and downloads media to temp files |
| `tg2max/db.py` | SQLite tracking of copied posts (`tg2max.db`) |

Media files are downloaded one post at a time and deleted right after sending. Posts are sent with a 2-second pause between them.

## Limitations

- **Unofficial automation.** tg2max controls the MAX web client the way a user would. This may conflict with MAX terms of use, and your account could be restricted. Use it at your own risk, ideally on low volumes.
- **Fragile selectors.** Any MAX web UI change can break posting. If it stops working, check the selectors in `tg2max/max_client.py`.
- **The MAX session expires.** When that happens, run `python -m tg2max.login_max` again.
- The MAX UI must be in English: the client finds buttons by their English labels (`Send message`, `Upload file`).
- Only photos and videos are transferred as media. Voice messages, documents, polls and stickers are not supported.

## License

MIT
