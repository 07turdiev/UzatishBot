from pyrogram import Client

from bot.config import SESSION_NAME, API_ID, API_HASH, BOT_TOKEN

app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    no_updates=False,
    workers=1,
)
