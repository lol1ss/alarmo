# Alarmo Map — Telethon + Heroku + Neon

## Overview

A Telegram user session reads new posts from public channel `@...`; a separate bot session publishes **new messages**, not forwards, to your channel. No admin privileges are required in the source channel. The bot must be an admin with posting permissions in the destination channel.

## 1. Create a Telegram API application

Visit https://my.telegram.org -> API Development Tools. Obtain `API_ID` and `API_HASH`. Subscribe the user account you intend to use to `@...`.

## 2. Generate the user session locally

Install Python 3.11+ on your own computer. Run:

```bash
python -m pip install -r requirements.txt
python create_session.py
```

Enter your `API_ID`, `API_HASH`, phone number, Telegram login code and 2FA password if requested. Save the printed `USER_SESSION` **privately** in Heroku Config Vars. It gives access to your Telegram account. Never share it or commit it to GitHub.

## 3. Neon database

Create a free PostgreSQL database at https://console.neon.tech and copy its complete connection string. Use it as `DATABASE_URL` in Heroku. Tables are created on first launch.

## 4. Heroku Config Vars

| Key | Value |
| --- | --- |
| `API_ID` | Numeric Telegram API ID |
| `API_HASH` | Telegram API hash |
| `USER_SESSION` | String produced by `create_session.py` |
| `BOT_TOKEN` | **New** bot token from BotFather |
| `SOURCE_CHANNEL` | `kpszsu` |
| `TARGET_CHANNEL` | `@your_channel` or numeric `-100...` |
| `DATABASE_URL` | Neon PostgreSQL connection URL |
| `STARTUP_HISTORY` | `50` (optional) |

## 5. Deploy

Upload files from the project root to GitHub. Connect GitHub in Heroku -> Deploy. Deploy the branch. Under Resources, enable **one** `worker` dyno. The Procfile is `worker: python bot.py` (no `web` process required).

Check logs in Heroku -> More -> View logs. On first startup, existing posts are skipped; future posts are copied. After restarts, up to 50 missed messages are replayed.

## Operational limitations

- Message text, Telegram text entities, photos and most videos are copied as **new** posts. They do not show the forward attribution.
- Telegram custom emoji and protected content may not copy, depending on permissions and Telegram restrictions.
- Media groups/albums are not yet grouped: each message is posted separately.
- Source edits/deletions are not mirrored.
- An error between successful send and database commit may cause a duplicate.
- Temporary downloaded media is stored on the ephemeral dyno filesystem, not Neon; large media can exhaust dyno storage or time limits.
- Neon free database may suspend while idle; the worker should reconnect if the database connection drops. This starter does not implement DB reconnection; restart the worker if that occurs.
- Telegram may limit API usage; delivery latency is not guaranteed. Do not use this as the sole critical air-raid alert source.
- Use an authorized account and respect Telegram/channel restrictions. Never publish your bot token or session string.
