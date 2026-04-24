from bot.config import SOURCE_CHANNEL, logger
from bot.channels import DESTINATION_CHANNELS
from bot.client import app
from bot.handlers import register_all

register_all()

if __name__ == "__main__":
    if not SOURCE_CHANNEL:
        logger.error("SOURCE_CHANNEL is not set (env or channels.json). Set it before running.")
    else:
        logger.info("Userbot starting...")
        logger.info(f"Manba kanal: {SOURCE_CHANNEL}")
        logger.info(f"Maqsad kanallar: {DESTINATION_CHANNELS}")
        app.run()
