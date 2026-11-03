# tg2max

Copy posts from a Telegram channel to a [MAX](https://max.ru) channel: text, photos, videos and albums, in chronological order.

MAX's official Bot API is only available to Russian businesses and self-employed, so tg2max talks to MAX the way its web client (web.max.ru) does: over the same internal WebSocket protocol, with no browser involved. Telegram is read through the regular user API (MTProto, via Telethon).

## Features

- One-time migration of the full channel history, or from a given date
- Watch mode: forwards new Telegram posts to MAX as they appear
- Albums are sent as one MAX message with a shared caption
- Text formatting (bold, italic, underline, strikethrough, links, quotes) is carried over as-is
- A post counts as copied only when MAX confirms it with a message id
- Already copied posts are tracked in a local SQLite DB, so reruns never duplicate
- Incremental runs: without `--since`, migration resumes after the last copied post
- Order is preserved: if a post fails, the run stops and the post is retried on the next run
- Lightweight: no browser, ~40 MB of dependencies

## Requirements

- Python 3.9+
- Telegram API credentials from https://my.telegram.org/apps
- A MAX account with permission to post in the target channel

## Setup

```bash
git clone https://github.com/Podogrev/tg2max.git
cd tg2max
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in the values
```

Log in to MAX once. A QR code is printed right in the terminal; scan it with the MAX mobile app:

```bash
python -m tg2max.login_max
```

The session is saved to `max_token.json` (readable by the owner only). On the first Telegram run you will be asked for a login code; the session is saved to `tg_session.session`.

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
| `tg2max/max_proto.py` | Minimal client for MAX's internal web protocol: login, photo/video upload, sending |
| `tg2max/max_client.py` | Posts one Telegram post (text, formatting, media or album) to the MAX channel |
| `tg2max/login_max.py` | QR login in the terminal |
| `tg2max/formatter.py` | Maps Telegram message entities to MAX formatting elements |
| `tg2max/media.py` | Detects the media type of a Telegram message |
| `tg2max/db.py` | SQLite tracking of copied posts (`tg2max.db`) |

Media files are downloaded one post at a time and deleted right after sending. Posts are sent with a 2-second pause between them.

The MAX side uses `wss://api.oneme.ru/websocket` with msgpack frames, as web.max.ru does. Upload hosts are signed by the Russian Trusted Root CA, so its certificate ships in `tg2max/rootca_ssl_rsa2022.crt`. The protocol details were cross-checked against [PyMax](https://github.com/MaxApiTeam/PyMax) (MIT).

## Limitations

- **Unofficial protocol.** tg2max uses MAX's undocumented internal API, not the official Bot API. This may conflict with MAX terms of use, and your account could be restricted. Use it at your own risk, ideally on low volumes.
- **It can break without notice.** If MAX changes its protocol, posting stops working until `tg2max/max_proto.py` is updated.
- **The MAX session expires.** tg2max then exits with `MaxSessionError`; run `python -m tg2max.login_max` again.
- Monospace and code blocks are sent, but the MAX web client currently shows them as plain text.
- Only photos and videos are transferred as media. Voice messages, documents, polls and stickers are not supported.

## License

MIT
