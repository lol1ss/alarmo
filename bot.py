import asyncio
import logging
import mimetypes
import os
import random
import json
import tempfile
from pathlib import Path

import aiohttp
import psycopg
from telethon import TelegramClient, events, errors
from telethon.tl import types
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('alarmo')

API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
USER_SESSION = os.environ['USER_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']
SOURCE = os.getenv('SOURCE_CHANNEL', 'kpszsu').strip().removeprefix('@')
TARGET = os.environ['TARGET_CHANNEL'].strip()
DATABASE_URL = os.environ['DATABASE_URL']
STARTUP_HISTORY = int(os.getenv('STARTUP_HISTORY', '50'))

user = TelegramClient(StringSession(USER_SESSION), API_ID, API_HASH,
                      auto_reconnect=True, connection_retries=None)
lock = asyncio.Lock()


def connect_db():
    conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=15)
    conn.execute('''CREATE TABLE IF NOT EXISTS source_state (
        source TEXT PRIMARY KEY, last_id BIGINT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS delivered (
        source TEXT NOT NULL, message_id BIGINT NOT NULL,
        target_message_id BIGINT, delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(source, message_id))''')
    return conn


def get_last(conn):
    row = conn.execute('SELECT last_id FROM source_state WHERE source=%s', (SOURCE,)).fetchone()
    return row[0] if row else None


def save_last(conn, message_id):
    conn.execute('''INSERT INTO source_state(source,last_id) VALUES (%s,%s)
        ON CONFLICT(source) DO UPDATE SET
        last_id=GREATEST(source_state.last_id,EXCLUDED.last_id)''', (SOURCE, message_id))


def delivered(conn, message_id):
    return conn.execute('SELECT 1 FROM delivered WHERE source=%s AND message_id=%s',
                        (SOURCE, message_id)).fetchone() is not None


def mark_delivered(conn, message_id, target_id):
    conn.execute('''INSERT INTO delivered(source,message_id,target_message_id)
        VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''', (SOURCE, message_id, target_id))
    save_last(conn, message_id)


class BotAPIError(Exception):
    def __init__(self, description, retry_after=None):
        super().__init__(description)
        self.retry_after = retry_after


async def bot_request(session, method, data, file_path=None, file_field=None):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/{method}'
    if file_path:
        form = aiohttp.FormData()
        for key, value in data.items():
            form.add_field(key, str(value))
        with open(file_path, 'rb') as handle:
            form.add_field(file_field, handle, filename=Path(file_path).name,
                           content_type=mimetypes.guess_type(file_path)[0] or 'application/octet-stream')
            async with session.post(url, data=form) as response:
                payload = await response.json(content_type=None)
    else:
        async with session.post(url, data=data) as response:
            payload = await response.json(content_type=None)
    if not payload.get('ok'):
        params = payload.get('parameters') or {}
        raise BotAPIError(payload.get('description', 'Unknown Telegram Bot API error'),
                          params.get('retry_after'))
    return payload['result']


def telegram_entities(entities):
    """Telethon and Bot API both use UTF-16 offsets and lengths."""
    converted = []
    for entity in entities or []:
        item = {'offset': entity.offset, 'length': entity.length}
        if isinstance(entity, types.MessageEntityCustomEmoji):
            item.update(type='custom_emoji', custom_emoji_id=str(entity.document_id))
        elif isinstance(entity, types.MessageEntityBold):
            item['type'] = 'bold'
        elif isinstance(entity, types.MessageEntityItalic):
            item['type'] = 'italic'
        elif isinstance(entity, types.MessageEntityUnderline):
            item['type'] = 'underline'
        elif isinstance(entity, types.MessageEntityStrike):
            item['type'] = 'strikethrough'
        elif isinstance(entity, types.MessageEntitySpoiler):
            item['type'] = 'spoiler'
        elif isinstance(entity, types.MessageEntityCode):
            item['type'] = 'code'
        elif isinstance(entity, types.MessageEntityPre):
            item.update(type='pre', language=entity.language or '')
        elif isinstance(entity, types.MessageEntityTextUrl):
            item.update(type='text_link', url=entity.url)
        elif isinstance(entity, types.MessageEntityMentionName):
            item.update(type='text_mention', user={'id': entity.user_id})
        elif isinstance(entity, types.MessageEntityBlockquote):
            item.update(type='blockquote', is_collapsed=bool(getattr(entity, 'collapsed', False)))
        elif isinstance(entity, types.MessageEntityUrl):
            item['type'] = 'url'
        elif isinstance(entity, types.MessageEntityEmail):
            item['type'] = 'email'
        elif isinstance(entity, types.MessageEntityPhone):
            item['type'] = 'phone_number'
        elif isinstance(entity, types.MessageEntityMention):
            item['type'] = 'mention'
        elif isinstance(entity, types.MessageEntityHashtag):
            item['type'] = 'hashtag'
        elif isinstance(entity, types.MessageEntityCashtag):
            item['type'] = 'cashtag'
        elif isinstance(entity, types.MessageEntityBotCommand):
            item['type'] = 'bot_command'
        else:
            continue
        converted.append(item)
    return converted


async def send_copy(session, msg):
    text = msg.message or ''
    entities = telegram_entities(msg.entities)
    log.info(
    "Message %s custom emojis: %s",
    msg.id,
    [
        e["custom_emoji_id"]
        for e in entities
        if e["type"] == "custom_emoji"
    ]
)
    if msg.media:
        with tempfile.TemporaryDirectory(prefix='alarmo_') as folder:
            path = await user.download_media(msg, file=folder)
            if not path:
                raise RuntimeError(f'Unable to download media for message {msg.id}')
            if Path(path).stat().st_size > 50 * 1024 * 1024:
                raise RuntimeError(f'Media exceeds Bot API 50 MB upload limit: message {msg.id}')
            mime = mimetypes.guess_type(path)[0] or ''
            if msg.photo:
                method, field = 'sendPhoto', 'photo'
            elif msg.video and mime.startswith('video/'):
                method, field = 'sendVideo', 'video'
            else:
                method, field = 'sendDocument', 'document'
            data = {'chat_id': TARGET}
            # Bot API captions are limited to 1024 characters after parsing.
            if text and len(text.encode('utf-16-le')) // 2 <= 1024:
                data['caption'] = text
                if entities:
                    data['caption_entities'] = json.dumps(entities, ensure_ascii=False)
            result = await bot_request(session, method, data, path, field)
            if text and len(text.encode('utf-16-le')) // 2 > 1024:
                await send_text(session, text, entities)
            return result['message_id']
    if text:
        return await send_text(session, text, entities)
    log.info('Skipping unsupported empty post %s', msg.id)
    return None


async def send_text(session, text, entities):
    data = {'chat_id': TARGET, 'text': text, 'disable_web_page_preview': 'true'}
    if entities:
        data['entities'] = json.dumps(entities, ensure_ascii=False)
    result = await bot_request(session, 'sendMessage', data)
    return result['message_id']


async def process(conn, session, msg):
    if msg.action or msg.id is None:
        return
    async with lock:
        if delivered(conn, msg.id):
            return
        for attempt in range(5):
            try:
                target_id = await send_copy(session, msg)
                if target_id is not None:
                    mark_delivered(conn, msg.id, target_id)
                    log.info('Copied source=%s id=%s target_id=%s', SOURCE, msg.id, target_id)
                else:
                    save_last(conn, msg.id)
                return
            except errors.FloodWaitError as exc:
                delay = exc.seconds + 2
                log.warning('User session FloodWait: %ss', delay)
                await asyncio.sleep(delay)
            except BotAPIError as exc:
                if exc.retry_after is not None:
                    delay = int(exc.retry_after) + 2
                    log.warning('Bot API rate limit: waiting %ss', delay)
                    await asyncio.sleep(delay)
                else:
                    log.error('Bot API error for message %s: %s', msg.id, exc)
                    if attempt == 4:
                        return
                    await asyncio.sleep(min(60, 2 ** attempt + random.random()))
            except Exception:
                log.exception('Attempt %s failed for message %s', attempt + 1, msg.id)
                if attempt == 4:
                    return
                await asyncio.sleep(min(60, 2 ** attempt + random.random()))


async def main():
    conn = connect_db()
    await user.start()
    me = await user.get_me()
    if me.bot:
        raise RuntimeError('USER_SESSION belongs to a bot; create a session for a real Telegram user')
    source_entity = await user.get_entity(f'@{SOURCE}')
    log.info('Connected to source @%s; target chat ID %s', SOURCE, TARGET)

    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        bot_info = await bot_request(session, 'getMe', {})
        log.info('Bot API connected as @%s', bot_info.get('username'))

        @user.on(events.NewMessage(chats=source_entity))
        async def on_new(event):
            await process(conn, session, event.message)

        last = get_last(conn)
        if last is None:
            newest = await user.get_messages(source_entity, limit=1)
            baseline = newest[0].id if newest else 0
            save_last(conn, baseline)
            log.info('First launch: baseline ID %s; old posts will not be copied', baseline)
        else:
            history = []
            async for msg in user.iter_messages(source_entity, min_id=last,
                                                reverse=True, limit=STARTUP_HISTORY):
                history.append(msg)
            for msg in history:
                await process(conn, session, msg)
            log.info('Startup catch-up processed %s messages', len(history))
        log.info('Listening for new posts from @%s', SOURCE)
        await user.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
