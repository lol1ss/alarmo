import os, re, time, json, logging, random
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import psycopg

SOURCE = os.getenv('SOURCE_CHANNEL', 'kpszsu').lstrip('@')
DEST = os.environ['TARGET_CHANNEL']
TOKEN = os.environ['BOT_TOKEN']
DB = os.environ['DATABASE_URL']
INTERVAL = max(10, int(os.getenv('POLL_SECONDS', '15')))
URL = f'https://t.me/s/{SOURCE}'
API = f'https://api.telegram.org/bot{TOKEN}'
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('alarmo')
http = requests.Session()
http.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; AlarmoMapFeed/1.0)'})


def fetch():
    r = http.get(URL, timeout=20, headers={'Cache-Control':'no-cache'})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    out = []
    for item in soup.select('.tgme_widget_message[data-post]'):
        match = re.fullmatch(re.escape(SOURCE) + r'/(\d+)', item.get('data-post', ''), re.I)
        if not match: continue
        node = item.select_one('.tgme_widget_message_text')
        text = node.get_text('\n', strip=True) if node else ''
        out.append((int(match.group(1)), text))
    if not out: raise RuntimeError('No messages found: page may have changed or access blocked')
    return sorted(set(out), key=lambda x:x[0])


def send(text):
    r = http.post(API + '/sendMessage', json={'chat_id':DEST, 'text':text, 'link_preview_options':{'is_disabled':True}}, timeout=25)
    try: payload = r.json()
    except ValueError: r.raise_for_status(); raise RuntimeError('Invalid Telegram response')
    if r.status_code == 429:
        wait = min(int(payload.get('parameters', {}).get('retry_after', 10)), 120)
        raise RuntimeError(f'Telegram rate limited; retry in {wait}s')
    r.raise_for_status()
    if not payload.get('ok'): raise RuntimeError(str(payload))
    return payload['result']['message_id']


def init(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS feed_state (source TEXT PRIMARY KEY, last_id BIGINT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS published (source TEXT NOT NULL, post_id BIGINT NOT NULL, destination_id BIGINT, published_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(source,post_id))')
    conn.commit()


def split_text(text, limit=3900):
    while text:
        if len(text)<=limit: yield text; break
        cut = text.rfind('\n', 0, limit)
        if cut < limit//2: cut=limit
        yield text[:cut]
        text=text[cut:].lstrip('\n')


def run_once(conn):
    posts=fetch()
    row=conn.execute('SELECT last_id FROM feed_state WHERE source=%s',(SOURCE,)).fetchone()
    if row is None:
        last=posts[-1][0]
        conn.execute('INSERT INTO feed_state(source,last_id) VALUES(%s,%s)',(SOURCE,last))
        conn.commit()
        log.info('Initialized at post %s; older posts skipped',last)
        return
    last=row[0]
    for post_id, body in posts:
        if post_id<=last: continue
        existing=conn.execute('SELECT 1 FROM published WHERE source=%s AND post_id=%s',(SOURCE,post_id)).fetchone()
        if existing:
            conn.execute('UPDATE feed_state SET last_id=%s WHERE source=%s',(post_id,SOURCE)); conn.commit(); last=post_id; continue
        link=f'https://t.me/{SOURCE}/{post_id}'
        content=(body+'\n\nИсточник: '+link) if body else ('Новая публикация: '+link)
        message_id=None
        for part in split_text(content):
            message_id=send(part)
        conn.execute('INSERT INTO published(source,post_id,destination_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(SOURCE,post_id,message_id))
        conn.execute('UPDATE feed_state SET last_id=%s WHERE source=%s',(post_id,SOURCE))
        conn.commit()
        last=post_id
        log.info('Published source post %s',post_id)


def main():
    log.info('Starting %s -> %s; interval=%ss',SOURCE,DEST,INTERVAL)
    while True:
        try:
            with psycopg.connect(DB, autocommit=False, connect_timeout=15) as conn:
                init(conn)
                run_once(conn)
        except Exception:
            log.exception('Poll failed; retrying')
        time.sleep(INTERVAL + random.uniform(0,2))

if __name__=='__main__': main()
