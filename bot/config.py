import os
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

# Logging — console + file
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
)

_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_log_format))
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger("bot")


def _parse_int(value: Optional[str], name: str) -> int:
    if value is None or value == "":
        raise RuntimeError(f"Missing required env: {name}")
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"Invalid integer for {name}: {value}")


API_ID = _parse_int(os.getenv("API_ID"), "API_ID")

API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise RuntimeError("Missing required env: API_HASH")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing required env: BOT_TOKEN")

SESSION_NAME = os.getenv("SESSION_NAME", "my_userbot")

ADMIN_USERS: List[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_USERS", "").split(",")
    if x.strip().isdigit()
]

# Website API
WEBSITE_API_URL = os.getenv("MADANIYAT_API_URL") or os.getenv("WEBSITE_API_URL", "")
WEBSITE_API_KEY = os.getenv("MADANIYAT_API_KEY") or os.getenv("WEBSITE_API_KEY", "")
ENABLE_WEBSITE_API = (
    os.getenv("ENABLE_WEBSITE_POST", os.getenv("ENABLE_WEBSITE_API", "false")).lower() == "true"
)

# Source channel
_source_env = os.getenv("SOURCE_CHANNEL")
SOURCE_CHANNEL = int(_source_env) if (_source_env and _source_env.strip("-+").isdigit()) else 0

# Pagination
PAGE_SIZE_REMOVE = 10
PAGE_SIZE_CHANNELS = 25
PAGE_SIZE_DIAGNOSE = 10

# JSON file
CHANNELS_FILE = "channels.json"
