# Alarmo Map — Telegram public channel mirror (Heroku)

Reads the public web preview at https://t.me/s/kpszsu and publishes new text posts to a Telegram channel via Bot API. Does not require logging into a personal Telegram account. This is polling, **not** guaranteed real-time delivery.

## Files
- `bot.py` — polling worker
- `Procfile` — Heroku worker process
- `requirements.txt` — Python dependencies

## Deploy
1. Create a Heroku app and provision Heroku Postgres (available plan).
2. In Settings → Config Vars set:
   - `BOT_TOKEN` — bot token from BotFather (never commit or share)
   - `TARGET_CHANNEL` — `@your_public_channel` or numeric channel ID
   - `SOURCE_CHANNEL` — `kpszsu` (optional; default)
   - `POLL_SECONDS` — `15` (optional; minimum 10)
   - `DATABASE_URL` — supplied automatically by Heroku Postgres
3. Deploy the repository via Heroku Git or connected GitHub.
4. In Resources, scale `worker` to 1 (and `web` to 0 if present), or run `heroku ps:scale worker=1 --app YOUR_APP`.
5. Inspect `heroku logs --tail --app YOUR_APP`.

## Behavior and limits
- First run initializes the cursor at the newest visible source post and sends nothing historical.
- Posts are processed in ascending ID order and recorded in Postgres.
- Text is copied, with a source link; media-only posts get a link. Formatting, media, edits and deletions are not mirrored.
- If the page is stale, HTML changes, or messages disappear from its preview before polling, posts may be delayed or missed.
- A crash after Telegram accepts a send but before DB commit can cause a duplicate. Multi-part posts can partially publish before a failure.
- Keep exactly one worker instance to avoid duplicate sends.
- Use this as supplementary information only, not a sole safety-critical alert source.
