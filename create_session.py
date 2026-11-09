import os
from getpass import getpass
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('API_ID: ').strip())
api_hash = getpass('API_HASH: ').strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print('\nUSER_SESSION (keep secret; paste into Heroku Config Vars):')
    print(client.session.save())
