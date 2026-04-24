"""Entry point — backward compatible with `python run.py`."""
from bot.config import SOURCE_CHANNEL, logger
from bot.channels import DESTINATION_CHANNELS
from bot.client import app
from bot.handlers import register_all

register_all()

if not SOURCE_CHANNEL:
    logger.error("SOURCE_CHANNEL is not set. Set it in .env before running.")
else:
    logger.info("Userbot starting...")
    logger.info(f"Manba kanal: {SOURCE_CHANNEL}")
    logger.info(f"Maqsad kanallar: {DESTINATION_CHANNELS}")
    app.run()
