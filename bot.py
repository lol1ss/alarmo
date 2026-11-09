import asyncio
import logging
import os
import random
from datetime import datetime, timezone

import psycopg
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('alarmo')

API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
USER_SESSION = os.environ['USER_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']
SOURCE = os.getenv('SOURCE_CHANNEL', 'kpszsu').lstrip('@')
TARGET = os.environ['TARGET_CHANNEL']
DATABASE_URL = os.environ['DATABASE_URL']
STARTUP_HISTORY = int(os.getenv('STARTUP_HISTORY', '50'))

user = TelegramClient(StringSession(USER_SESSION), API_ID, API_HASH, auto_reconnect=True, connection_retries=None)
bot = TelegramClient(StringSession(), API_ID, API_HASH, auto_reconnect=True, connection_retries=None)
lock = asyncio.Lock()


def connect_db():
    conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=15)
    conn.execute('''CREATE TABLE IF NOT EXISTS source_state (
        source TEXT PRIMARY KEY,
        last_id BIGINT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS delivered (
        source TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        target_message_id BIGINT,
        delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(source, message_id)
    )''')
    return conn


def get_last(conn):
    row = conn.execute('SELECT last_id FROM source_state WHERE source=%s', (SOURCE,)).fetchone()
    return row[0] if row else None


def save_last(conn, message_id):
    conn.execute('''INSERT INTO source_state(source,last_id) VALUES (%s,%s)
        ON CONFLICT(source) DO UPDATE SET last_id=GREATEST(source_state.last_id,EXCLUDED.last_id)''', (SOURCE, message_id))


def delivered(conn, message_id):
    return conn.execute('SELECT 1 FROM delivered WHERE source=%s AND message_id=%s', (SOURCE, message_id)).fetchone() is not None


def mark_delivered(conn, message_id, target_id):
    conn.execute('INSERT INTO delivered(source,message_id,target_message_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                 (SOURCE, message_id, target_id))
    save_last(conn, message_id)


async def send_copy(msg):
    # Send as a NEW post, never forward. Telethon preserves Telegram text entities.
    if msg.media:
        try:
            # File-like bytes are kept in memory; limit memory use on small dynos.
            # For large videos, a temporary file is used and cleaned up.
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory(prefix='alarmo_') as folder:
                path = await user.download_media(msg, file=folder)
                if path:
                    result = await bot.send_file(TARGET, path, caption=msg.message or '',
                                                 formatting_entities=msg.entities or None,
                                                 supports_streaming=True)
                    return result.id
        except Exception:
            log.exception('Media transfer failed for %s; will retry later', msg.id)
            raise
    if msg.message:
        result = await bot.send_message(TARGET, msg.message,
                                        formatting_entities=msg.entities or None,
                                        link_preview=False)
        return result.id
    log.info('Skipping unsupported empty post %s', msg.id)
    return None


async def process(conn, msg):
    if msg.action or msg.id is None:
        return
    async with lock:
        if delivered(conn, msg.id):
            return
        for attempt in range(5):
            try:
                target_id = await send_copy(msg)
                if target_id is not None:
                    mark_delivered(conn, msg.id, target_id)
                    log.info('Copied source=%s id=%s target_id=%s', SOURCE, msg.id, target_id)
                return
            except errors.FloodWaitError as exc:
                delay = exc.seconds + 2
                log.warning('FloodWait: %ss', delay)
                await asyncio.sleep(delay)
            except Exception:
                if attempt == 4:
                    log.exception('Message %s failed after retries', msg.id)
                    return
                await asyncio.sleep(min(60, 2 ** attempt + random.random()))


async def main():
    conn = connect_db()
    await user.start()
    await bot.start(bot_token=BOT_TOKEN)
    source_entity = await user.get_entity(SOURCE)
    target_entity = await bot.get_entity(TARGET)
    log.info('Connected source=%s target=%s', source_entity.id, target_entity.id)

    last = get_last(conn)
    if last is None:
        newest = await user.get_messages(source_entity, limit=1)
        baseline = newest[0].id if newest else 0
        save_last(conn, baseline)
        log.info('First launch: baseline ID %s; old posts will not be copied', baseline)
    else:
        # Catch up after worker restarts; limited to avoid huge backlogs.
        history = []
        async for msg in user.iter_messages(source_entity, min_id=last, reverse=True, limit=STARTUP_HISTORY):
            history.append(msg)
        for msg in history:
            await process(conn, msg)
        log.info('Startup catch-up processed %s messages', len(history))

    @user.on(events.NewMessage(chats=source_entity))
    async def on_new(event):
        await process(conn, event.message)

    log.info('Listening for new posts from @%s', SOURCE)
    await user.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
