import os
import math
import json
import asyncio
import logging
import io
import re
import tempfile
from typing import Dict, List, Optional, Tuple, Union, Set
from html import escape as html_escape

import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    MessageEntity,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.enums import MessageEntityType
from pyrogram.errors import FloodWait, RPCError


# Load environment variables
load_dotenv()


# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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

# Admin users (comma-separated user IDs)
ADMIN_USERS: List[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_USERS", "").split(",")
    if x.strip().isdigit()
]

# Website API Configuration
WEBSITE_API_URL = os.getenv("WEBSITE_API_URL", "")
WEBSITE_API_KEY = os.getenv("WEBSITE_API_KEY", "")
ENABLE_WEBSITE_API = os.getenv("ENABLE_WEBSITE_API", "false").lower() == "true"


# Source channel from .env only
_source_env = os.getenv("SOURCE_CHANNEL")
SOURCE_CHANNEL = int(_source_env) if (_source_env and _source_env.strip("-+").isdigit()) else 0


# JSON persistence
CHANNELS_FILE = "channels.json"


def load_channels() -> Dict[int, str]:
    """Load destination channels from channels.json."""
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        dest_map = data.get("destination_channels", {}) or {}
        dest_channels: Dict[int, str] = {}
        for k, v in dest_map.items():
            try:
                dest_channels[int(k)] = str(v)
            except (ValueError, TypeError):
                continue
        return dest_channels
    except FileNotFoundError:
        logger.info("channels.json not found")
        return {}
    except json.JSONDecodeError:
        logger.error("Failed to parse channels.json")
        return {}


def save_channels(destination_channels: Dict[int, str]) -> None:
    """Persist destination channels to JSON file."""
    payload = {
        "destination_channels": destination_channels,
    }
    try:
        with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving channels.json: {e}")


# Load initial channels
DESTINATION_CHANNELS = load_channels()
if not isinstance(SOURCE_CHANNEL, int):
    try:
        SOURCE_CHANNEL = int(SOURCE_CHANNEL)
    except Exception:
        SOURCE_CHANNEL = 0


# In-memory state
media_groups: Dict[str, List[Message]] = {}
media_group_timers: Dict[str, asyncio.Task] = {}
CHECK_CHANNEL_STATE: Dict[int, dict] = {}
REMOVE_CHANNEL_STATE: Dict[int, dict] = {}
VALID_DEST_CHANNELS: Set[int] = set()
INVALID_DEST_CHANNELS: Set[int] = set()
PENDING_FORWARDS: Dict[str, dict] = {}


# Client instance
app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    no_updates=False,
    workers=1,
)


# Helpers
async def safe_forward_messages(client: Client, from_chat_id: int, message_ids: List[int], dest_channel: int) -> bool:
    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        try:
            await client.forward_messages(
                chat_id=dest_channel,
                from_chat_id=from_chat_id,
                message_ids=message_ids,
            )
            return True
        except FloodWait as e:
            wait = getattr(e, "value", None) or getattr(e, "x", None) or getattr(e, "seconds", None) or 30
            logger.warning(f"FloodWait {wait}s while forwarding to {dest_channel}; sleeping...")
            await asyncio.sleep(int(wait) + 1)
            attempt += 1
        except RPCError as e:
            logger.error(f"RPCError while forwarding to {dest_channel}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error while forwarding to {dest_channel}: {e}")
            await asyncio.sleep(1)
            attempt += 1
    logger.error(f"Failed to forward messages to {dest_channel} after {max_retries} attempts")
    return False


async def ensure_destination_access(client: Client, dest_channel: int) -> bool:
    if dest_channel in VALID_DEST_CHANNELS:
        return True
    if dest_channel in INVALID_DEST_CHANNELS:
        logger.debug(f"Destination {dest_channel} is in invalid cache, skipping")
        return False
    try:
        chat = await client.get_chat(dest_channel)
        me = await client.get_chat_member(chat.id, "me")
        can_post = bool(getattr(getattr(me, 'privileges', None), 'can_post_messages', False))
        if not can_post:
            logger.error(
                f"Bot has no post permission in destination '{chat.title}' ({dest_channel}). "
                f"Add the bot as admin with 'Post Messages' permission."
            )
            INVALID_DEST_CHANNELS.add(dest_channel)
            return False
        VALID_DEST_CHANNELS.add(dest_channel)
        logger.info(f"✅ Destination '{chat.title}' ({dest_channel}) is accessible")
        return True
    except Exception as e:
        error_msg = str(e)
        if "PEER_ID_INVALID" in error_msg or "Peer id invalid" in error_msg:
            logger.error(
                f"❌ Destination {dest_channel}: Bot cannot access this channel. "
                f"Possible reasons:\n"
                f"  1. Bot is not added to the channel\n"
                f"  2. Bot token is wrong (different bot was added as admin)\n"
                f"  3. Channel ID is incorrect\n"
                f"  4. Channel was deleted\n"
                f"Solution: Remove channel with /remove_channel, then re-add with /check_channel"
            )
        else:
            logger.error(
                f"Cannot access destination {dest_channel}: {error_msg}. "
                f"Make sure the bot is added as admin to that channel."
            )
        logger.error(
            f"Full error details: {type(e).__name__}: {error_msg}"
        )
        INVALID_DEST_CHANNELS.add(dest_channel)
    return False


async def download_media_to_temp(client: Client, message: Message) -> Optional[str]:
    """Download media from message to a temporary file."""
    if not message.photo and not message.video and not message.document:
        return None
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_path = temp_file.name
        temp_file.close()
        await client.download_media(message, file_name=temp_path)
        return temp_path
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return None


async def send_to_website_api(
    lang_data: Dict[str, Dict[str, str]],
    image_paths: List[str]
) -> Tuple[bool, str]:
    """Send post data to website API.
    
    lang_data format: {
        "uz": {"title": "...", "body": "...", "short_description": "..."},
        "kr": {"title": "...", "body": "...", "short_description": "..."},
        ...
    }
    """
    if not WEBSITE_API_URL or not WEBSITE_API_KEY:
        return False, "API not configured"
    
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            
            # Add text fields per language
            for lang in ["uz", "ru", "en", "kr"]:
                ld = lang_data.get(lang, lang_data.get("uz", {}))
                data.add_field(f"title_{lang}", ld.get("title", "Yangilik"))
                data.add_field(f"body_{lang}", ld.get("body", ""))
                data.add_field(f"short_description_{lang}", ld.get("short_description", ld.get("title", "")[:150]))
            
            # Add images
            if image_paths:
                with open(image_paths[0], "rb") as f:
                    data.add_field(
                        "image",
                        f.read(),
                        filename=os.path.basename(image_paths[0]),
                        content_type="image/jpeg"
                    )
                for img_path in image_paths[1:]:
                    with open(img_path, "rb") as f:
                        data.add_field(
                            "additional_images",
                            f.read(),
                            filename=os.path.basename(img_path),
                            content_type="image/jpeg"
                        )
            
            headers = {"X-API-KEY": WEBSITE_API_KEY}
            async with session.post(WEBSITE_API_URL, data=data, headers=headers) as response:
                response_text = await response.text()
                if response.status == 200 or response.status == 201:
                    logger.info("✅ Website API: Post created successfully")
                    return True, "Success"
                else:
                    logger.error(f"❌ Website API error: {response.status} - {response_text}")
                    return False, f"Status {response.status}"
    except Exception as e:
        logger.error(f"❌ Website API exception: {e}")
        return False, str(e)
    finally:
        for path in image_paths:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass


def telegram_to_html(text: str, entities: Optional[List[MessageEntity]]) -> str:
    """Convert Telegram message with entities to HTML."""
    if not text:
        return ""
    
    if not entities:
        escaped = html_escape(text)
        return escaped.replace("\n", "<br>")
    
    sorted_entities = sorted(entities, key=lambda e: e.offset)
    result = []
    last_offset = 0
    
    for entity in sorted_entities:
        if entity.offset > last_offset:
            before_text = text[last_offset:entity.offset]
            result.append(html_escape(before_text))
        
        entity_text = text[entity.offset:entity.offset + entity.length]
        escaped_text = html_escape(entity_text)
        
        if entity.type == MessageEntityType.BOLD:
            result.append(f"<strong>{escaped_text}</strong>")
        elif entity.type == MessageEntityType.ITALIC:
            result.append(f"<em>{escaped_text}</em>")
        elif entity.type == MessageEntityType.UNDERLINE:
            result.append(f"<u>{escaped_text}</u>")
        elif entity.type == MessageEntityType.STRIKETHROUGH:
            result.append(f"<s>{escaped_text}</s>")
        elif entity.type == MessageEntityType.CODE:
            result.append(f"<code>{escaped_text}</code>")
        elif entity.type == MessageEntityType.PRE:
            result.append(f"<pre>{escaped_text}</pre>")
        elif entity.type == MessageEntityType.TEXT_LINK:
            url = entity.url or ""
            result.append(f'<a href="{html_escape(url)}" target="_blank">{escaped_text}</a>')
        elif entity.type == MessageEntityType.URL:
            result.append(f'<a href="{html_escape(entity_text)}" target="_blank">{escaped_text}</a>')
        elif entity.type == MessageEntityType.MENTION:
            result.append(f'<a href="https://t.me/{entity_text[1:]}" target="_blank">{escaped_text}</a>')
        elif entity.type == MessageEntityType.HASHTAG:
            result.append(escaped_text)
        else:
            result.append(escaped_text)
        
        last_offset = entity.offset + entity.length
    
    if last_offset < len(text):
        result.append(html_escape(text[last_offset:]))
    
    html_text = "".join(result)
    html_text = html_text.replace("\n", "<br>")
    return html_text


def format_text_for_api(text: str) -> str:
    """Format text for website API - remove hashtags, convert newlines to <br>."""
    if not text:
        return ""
    
    lines = text.split("\n")
    filtered_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered_lines.append("")
            continue
        
        words = stripped.split()
        if words and all(word.startswith("#") for word in words):
            continue
        
        clean_line = re.sub(r'#\w+', '', line)
        clean_line = re.sub(r'  +', ' ', clean_line).strip()
        
        if clean_line:
            filtered_lines.append(clean_line)
    
    result = "<br>".join(filtered_lines)
    result = re.sub(r'(<br>){3,}', '<br><br>', result)
    return result.strip()


def _make_forward_token_for_message(chat_id: int, message_id: int) -> str:
    return f"msg:{chat_id}:{message_id}"


def _make_forward_token_for_group(media_group_id: str) -> str:
    return f"mg:{media_group_id}"


async def request_forward_approval(client: Client, token: str, source_title: str, total_targets: int) -> None:
    text = (
        "Yangi xabar keldi.\n"
        f"Manba: {source_title}\n"
        f"Maqsad kanallar: {total_targets} ta\n\n"
        "Yuborilsinmi?"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ha", callback_data=f"fw_yes:{token}"),
            InlineKeyboardButton("❌ Yo'q", callback_data=f"fw_no:{token}"),
        ]
    ])
    entry = PENDING_FORWARDS.get(token)
    if entry is None:
        return
    entry.setdefault("admin_message_ids", {})
    for admin_id in ADMIN_USERS:
        try:
            msg = await client.send_message(admin_id, text, reply_markup=keyboard)
            entry["admin_message_ids"][admin_id] = msg.id
        except Exception as e:
            logger.warning(f"Approval prompt not delivered to admin {admin_id}: {e}")


def get_destinations_sorted() -> List[Tuple[int, str]]:
    # Stable order by title then id
    return sorted(DESTINATION_CHANNELS.items(), key=lambda kv: (kv[1] or "", kv[0]))


PAGE_SIZE_REMOVE = 10
PAGE_SIZE_CHANNELS = 25
PAGE_SIZE_DIAGNOSE = 10


def create_remove_list_markup(page: int = 0, per_page: int = PAGE_SIZE_REMOVE) -> Optional[InlineKeyboardMarkup]:
    items = get_destinations_sorted()
    total = len(items)
    if total == 0:
        return None
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, total)
    buttons: List[List[InlineKeyboardButton]] = []
    for idx, (channel_id, title) in enumerate(items[start:end], start=start + 1):
        label = f"{idx}. {title}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"remove_channel_{channel_id}")
        ])
    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"remove_list_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"remove_list_page_{page + 1}"))
        buttons.append(nav_row)
    # Cancel row
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_remove")])
    return InlineKeyboardMarkup(buttons)


# Input parsing helper
def parse_chat_identifier(raw: str) -> Union[int, str]:
    s = (raw or "").strip()
    if not s:
        return s
    lower = s.lower()
    # t.me links
    if lower.startswith("https://t.me/") or lower.startswith("http://t.me/") or lower.startswith("t.me/"):
        parts = s.split("/")
        # find index of domain
        try:
            idx = next(i for i, p in enumerate(parts) if p.endswith("t.me") or p.endswith("t.me:"))
        except StopIteration:
            idx = 2  # best-effort fallback
        path = parts[idx + 1:]
        if not path:
            return s
        if path[0] == 'c' and len(path) >= 2 and path[1].lstrip("-+ ").isdigit():
            # t.me/c/<internalId>/...
            digits = path[1].lstrip("-+ ")
            return int(f"-100{digits}")
        # treat first path segment as username
        username = path[0].lstrip('@')
        return username
    # @username
    if s.startswith('@'):
        return s[1:]
    # numeric ids
    digits = s.lstrip('+- ')
    if digits.isdigit():
        if s.startswith('-100'):
            try:
                return int(s)
            except ValueError:
                pass
        # convert plain numeric to -100 prefixed channel id
        return int(f"-100{digits}")
    return s


# Command handlers
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def help_handler(client: Client, message: Message) -> None:
    is_admin = message.from_user and message.from_user.id in ADMIN_USERS
    if not is_admin:
        await message.reply_text("❌ Sizda bu botdan foydalanish huquqi mavjud emas")
        return
    help_text = (
        "👋 Bu bot manba kanaldan maqsad kanallarga xabarlarni forward qiladi.\n\n"
    )
    help_text += (
        "📝 Admin buyruqlari:\n"
        "• /check_channel – Yangi kanal qo'shish\n"
        "• /remove_channel – Kanalni o'chirish\n"
        "• /channels – Kanallar ro'yxati\n"
        "• /diagnose – Maqsad kanallarga kirish va ruxsatlarni tekshirish\n"
        "• /clear_cache – Invalid kanallar cache'ini tozalash\n\n"
    )
    await message.reply_text(help_text)


async def _make_channels_page(page: int = 0, per_page: int = PAGE_SIZE_CHANNELS) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    items = get_destinations_sorted()
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, total)
    lines = [f"📄 Kanallar ro'yxati ({total} ta):\n"]
    for idx, (cid, title) in enumerate(items[start:end], start=start + 1):
        lines.append(f"{idx}. {title}\n  ID: {cid}")
    text = "\n".join(lines)
    # nav
    nav_row: List[InlineKeyboardButton] = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"channels_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"channels_page_{page + 1}"))
    markup = InlineKeyboardMarkup([nav_row]) if nav_row else None
    return text, markup


async def _make_diagnose_page(client: Client, page: int = 0, per_page: int = PAGE_SIZE_DIAGNOSE) -> Tuple[str, InlineKeyboardMarkup]:
    items = get_destinations_sorted()
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, total)
    lines: List[str] = [f"🧪 Diagnose ({total} kanal):\n"]
    for cid, title in items[start:end]:
        try:
            chat = await client.get_chat(cid)
            member = await client.get_chat_member(chat.id, "me")
            can_post = bool(getattr(getattr(member, 'privileges', None), 'can_post_messages', False))
            can_edit = bool(getattr(getattr(member, 'privileges', None), 'can_edit_messages', False))
            lines.append(
                f"✅ {title} ({cid})\n   post={can_post}, edit={can_edit}"
            )
        except Exception as e:
            lines.append(
                f"❌ {title} ({cid}) – {e}"
            )
    text = "\n".join(lines)
    nav_row: List[InlineKeyboardButton] = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"diagnose_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"diagnose_page_{page + 1}"))
    action_row = [InlineKeyboardButton("📄 Hisobot (.txt)", callback_data="diagnose_download")]
    rows: List[List[InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.append(action_row)
    markup = InlineKeyboardMarkup(rows)
    return text, markup


async def _diagnose_full_report(client: Client) -> bytes:
    items = get_destinations_sorted()
    buf = io.StringIO()
    buf.write("Diagnose to'liq hisobot\n\n")
    for cid, title in items:
        try:
            chat = await client.get_chat(cid)
            member = await client.get_chat_member(chat.id, "me")
            can_post = bool(getattr(getattr(member, 'privileges', None), 'can_post_messages', False))
            can_edit = bool(getattr(getattr(member, 'privileges', None), 'can_edit_messages', False))
            buf.write(f"[OK] {title} ({cid}) post={can_post}, edit={can_edit}\n")
        except Exception as e:
            buf.write(f"[ERR] {title} ({cid}) {e}\n")
    content = buf.getvalue().encode("utf-8")
    buf.close()
    return content


@app.on_message(filters.command("diagnose") & filters.private)
async def diagnose_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    if not DESTINATION_CHANNELS:
        await message.reply_text("ℹ️ Hozircha kanallar yo'q.")
        return
    text, markup = await _make_diagnose_page(client, page=0)
    await message.reply_text(text, reply_markup=markup)


@app.on_message(filters.command("clear_cache") & filters.private)
async def clear_cache_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    cleared_invalid = len(INVALID_DEST_CHANNELS)
    cleared_valid = len(VALID_DEST_CHANNELS)
    INVALID_DEST_CHANNELS.clear()
    VALID_DEST_CHANNELS.clear()
    await message.reply_text(
        f"✅ Cache tozalandi.\n"
        f"Invalid kanallar: {cleared_invalid} ta\n"
        f"Valid kanallar: {cleared_valid} ta\n\n"
        f"Endi kanallar qayta tekshiriladi."
    )
    logger.info(f"Cache cleared by admin {message.from_user.id}: {cleared_invalid} invalid, {cleared_valid} valid")


@app.on_message(filters.command("channels") & filters.private)
async def list_channels_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    if not DESTINATION_CHANNELS:
        await message.reply_text("ℹ️ Hozircha kanallar yo'q.")
        return
    text, markup = await _make_channels_page(page=0)
    await message.reply_text(text, reply_markup=markup if markup else None)


@app.on_message(filters.command("check_channel") & filters.private)
async def check_channel_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    user_id = message.from_user.id
    CHECK_CHANNEL_STATE[user_id] = {"waiting_for_username": True}
    await message.reply_text(
        "📝 Kanal username yoki ID ni yuboring.\n"
        "Masalan: @kanal, -1001234567890, 1234567890 yoki t.me/kanal"
    )


@app.on_message(filters.command("remove_channel") & filters.private)
async def remove_channel_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    if not DESTINATION_CHANNELS:
        await message.reply_text("❌ Kanallar ro'yxati bo'sh!")
        return
    user_id = message.from_user.id
    REMOVE_CHANNEL_STATE[user_id] = {"waiting_for_channel": True, "page": 0}
    markup = create_remove_list_markup(page=0)
    await message.reply_text(
        "📋 O'chirish uchun kanal tanlang (tugmadan foydalaning):",
        reply_markup=markup,
    )


@app.on_message(filters.private & filters.text)
async def handle_text_messages(client: Client, message: Message) -> None:
    if message.text.startswith('/'):
        return
    user_id = message.from_user.id if message.from_user else 0
    text = message.text.strip()

    if user_id in CHECK_CHANNEL_STATE and CHECK_CHANNEL_STATE[user_id].get("waiting_for_username"):
        await process_channel_check_request(client, message, user_id, text)
        return

    if user_id in REMOVE_CHANNEL_STATE and REMOVE_CHANNEL_STATE[user_id].get("waiting_for_channel"):
        await process_channel_remove_request(client, message, user_id, text)
        return


async def process_channel_check_request(client: Client, message: Message, user_id: int, channel: str) -> None:
    try:
        chat_id_or_username = parse_chat_identifier(channel)
        chat = await client.get_chat(chat_id_or_username)
        me = await client.get_chat_member(chat.id, "me")

        can_post = bool(getattr(getattr(me, 'privileges', None), 'can_post_messages', False))
        can_edit = bool(getattr(getattr(me, 'privileges', None), 'can_edit_messages', False))
        can_delete = bool(getattr(getattr(me, 'privileges', None), 'can_delete_messages', False))

        permissions_text = (
            "📊 Huquqlar:\n"
            f"• Xabar yuborish: {'✅' if can_post else '❌'}\n"
            f"• Xabarlarni tahrirlash: {'✅' if can_edit else '❌'}\n"
            f"• Xabarlarni o'chirish: {'✅' if can_delete else '❌'}\n"
        )
        has_required_permissions = can_post and can_edit

        CHECK_CHANNEL_STATE[user_id] = {
            "chat_id": chat.id,
            "title": chat.title,
            "username": chat.username,
            "has_permissions": has_required_permissions,
        }

        keyboard: Optional[InlineKeyboardMarkup] = None
        if has_required_permissions:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Ha", callback_data=f"add_channel_{chat.id}"),
                    InlineKeyboardButton("❌ Yo'q", callback_data="cancel_add"),
                ]
            ])

        response = (
            "📡 Kanal haqida ma'lumot:\n"
            f"• Nomi: {chat.title}\n"
            f"• ID: {chat.id}\n"
            f"• Username: {chat.username or 'Mavjud emas'}\n"
            f"{permissions_text}"
        )
        if has_required_permissions:
            response += "\n💡 Kanalni ro'yxatga qo'shishni xohlaysizmi?"

        await message.reply_text(response, reply_markup=keyboard)

        if not has_required_permissions:
            CHECK_CHANNEL_STATE.pop(user_id, None)

    except Exception as e:
        CHECK_CHANNEL_STATE.pop(user_id, None)
        await message.reply_text(f"❌ Xatolik yuz berdi: {e}\n\nBotni kanalga admin qilib, qayta urinib ko'ring.")


async def process_channel_remove_request(client: Client, message: Message, user_id: int, channel: str) -> None:
    try:
        # Normalize possible -100 prefix handling and partial ID
        chat_id: Optional[int] = None
        if channel.replace('-', '').isdigit():
            try:
                chat_id = int(channel)
            except ValueError:
                chat_id = None
        else:
            # Try resolving username to id
            try:
                chat = await client.get_chat(channel)
                chat_id = int(chat.id)
            except Exception:
                chat_id = None

        if chat_id is not None and chat_id not in DESTINATION_CHANNELS:
            # try matching by suffix
            for dest_id in list(DESTINATION_CHANNELS.keys()):
                if str(dest_id).endswith(str(abs(chat_id))) or str(abs(dest_id)).endswith(str(abs(chat_id))):
                    chat_id = dest_id
                    break    

        if chat_id is None or chat_id not in DESTINATION_CHANNELS:
            REMOVE_CHANNEL_STATE.pop(user_id, None)
            await message.reply_text("❌ Bu kanal ro'yxatda topilmadi!")
            return

        channel_name = DESTINATION_CHANNELS[chat_id]
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ha", callback_data=f"remove_channel_{chat_id}"),
                InlineKeyboardButton("❌ Yo'q", callback_data="cancel_remove"),
            ]
        ])
        await message.reply_text(
            "❓ Haqiqatdan ham ushbu kanalni o'chirmoqchimisiz?\n\n"
            f"• Nomi: {channel_name}\n"
            f"• ID: {chat_id}",
            reply_markup=keyboard,
        )
        REMOVE_CHANNEL_STATE[user_id].update({"chat_id": chat_id, "channel_name": channel_name})
    except Exception as e:
        REMOVE_CHANNEL_STATE.pop(user_id, None)
        await message.reply_text(f"❌ Xatolik yuz berdi: {e}")


@app.on_callback_query()
async def handle_channel_callback(client: Client, callback_query: CallbackQuery) -> None:
    user_id = callback_query.from_user.id
    data = callback_query.data or ""

    try:
        if data == "noop":
            await callback_query.answer()
            return
        if data.startswith("remove_list_page_"):
            # Pagination for remove list
            if user_id not in REMOVE_CHANNEL_STATE:
                await callback_query.answer("Holat topilmadi.")
                return
            try:
                new_page = int(data.split("_")[-1])
            except ValueError:
                await callback_query.answer("Noto'g'ri sahifa.")
                return
            REMOVE_CHANNEL_STATE[user_id]["page"] = max(0, new_page)
            markup = create_remove_list_markup(page=new_page)
            try:
                await callback_query.message.edit_reply_markup(markup)
            except Exception:
                try:
                    await callback_query.message.edit_text(
                        "📋 O'chirish uchun kanal tanlang (tugmadan foydalaning):",
                        reply_markup=markup,
                    )
                except Exception:
                    pass
            await callback_query.answer()
            return
        if data.startswith("channels_page_"):
            if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
                await callback_query.answer("Ruxsat yo'q")
                return
            try:
                new_page = int(data.split("_")[-1])
            except ValueError:
                await callback_query.answer("Noto'g'ri sahifa")
                return
            text, markup = await _make_channels_page(page=new_page)
            try:
                await callback_query.message.edit_text(text, reply_markup=markup if markup else None)
            except Exception:
                pass
            await callback_query.answer()
            return
        if data.startswith("diagnose_page_"):
            if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
                await callback_query.answer("Ruxsat yo'q")
                return
            try:
                new_page = int(data.split("_")[-1])
            except ValueError:
                await callback_query.answer("Noto'g'ri sahifa")
                return
            text, markup = await _make_diagnose_page(client, page=new_page)
            try:
                await callback_query.message.edit_text(text, reply_markup=markup)
            except Exception:
                pass
            await callback_query.answer()
            return
        if data == "diagnose_download":
            if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
                await callback_query.answer("Ruxsat yo'q")
                return
            await callback_query.answer("Yuklanmoqda...")
            content = await _diagnose_full_report(client)
            doc = io.BytesIO(content)
            doc.name = "diagnose_report.txt"
            try:
                await client.send_document(callback_query.from_user.id, doc, caption="Diagnose to'liq hisobot")
            except Exception:
                pass
            return
        if data.startswith("fw_yes:") or data.startswith("fw_no:"):
            approve = data.startswith("fw_yes:")
            token = data.split(":", 1)[1]
            entry = PENDING_FORWARDS.get(token)
            if not entry:
                await callback_query.answer("Bu so'rov topilmadi yoki muddati o'tgan.")
                return
            status = entry.get("status")
            if status != "waiting":
                await callback_query.answer("Allaqachon javob berilgan.")
                return
            
            if not approve:
                entry["status"] = "cancelled"
                await callback_query.answer("Bekor qilindi")
                for admin_id, msg_id in list(entry.get("admin_message_ids", {}).items()):
                    try:
                        await client.edit_message_text(admin_id, msg_id, "❌ Yuborish bekor qilindi.")
                    except Exception:
                        continue
                return
            
            # Show destination selection UI
            entry["status"] = "selecting"
            entry["destinations"] = {"channels": True, "website": True}
            await callback_query.answer("Manzillarni tanlang")
            
            ch_check = "☑️" if entry["destinations"]["channels"] else "◻️"
            web_check = "☑️" if entry["destinations"]["website"] else "◻️"
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"{ch_check} Kanallar", callback_data=f"dest_ch:{token}"),
                    InlineKeyboardButton(f"{web_check} Sayt", callback_data=f"dest_web:{token}")
                ],
                [InlineKeyboardButton("✅ Yuborish", callback_data=f"dest_confirm:{token}")]
            ])
            
            try:
                await callback_query.message.edit_text(
                    "📍 Qayerga yuborish kerak?\n\nTanlang va \"Yuborish\" tugmasini bosing:",
                    reply_markup=keyboard
                )
            except Exception:
                pass
            return
        
        # Handle destination toggle buttons
        if data.startswith("dest_ch:") or data.startswith("dest_web:"):
            is_channels = data.startswith("dest_ch:")
            token = data.split(":", 1)[1]
            entry = PENDING_FORWARDS.get(token)
            if not entry:
                await callback_query.answer("So'rov topilmadi.")
                return
            if entry.get("status") != "selecting":
                await callback_query.answer("Bu so'rov aktiv emas.")
                return
            
            key = "channels" if is_channels else "website"
            entry["destinations"][key] = not entry["destinations"].get(key, True)
            
            ch_check = "☑️" if entry["destinations"]["channels"] else "◻️"
            web_check = "☑️" if entry["destinations"]["website"] else "◻️"
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"{ch_check} Kanallar", callback_data=f"dest_ch:{token}"),
                    InlineKeyboardButton(f"{web_check} Sayt", callback_data=f"dest_web:{token}")
                ],
                [InlineKeyboardButton("✅ Yuborish", callback_data=f"dest_confirm:{token}")]
            ])
            
            await callback_query.answer()
            try:
                await callback_query.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
            return
        
        # Handle destination confirm
        if data.startswith("dest_confirm:"):
            token = data.split(":", 1)[1]
            entry = PENDING_FORWARDS.get(token)
            if not entry:
                await callback_query.answer("So'rov topilmadi.")
                return
            if entry.get("status") != "selecting":
                await callback_query.answer("Bu so'rov aktiv emas.")
                return
            
            destinations = entry.get("destinations", {"channels": True, "website": True})
            if not destinations.get("channels") and not destinations.get("website"):
                await callback_query.answer("Kamida bitta manzil tanlang!")
                return
            
            await callback_query.answer("Yuborish boshlandi...")
            try:
                await callback_query.message.edit_text("⏳ Yuborilmoqda...")
            except Exception:
                pass

            from_chat_id = int(entry["from_chat_id"]) if "from_chat_id" in entry else None
            message_ids = list(entry.get("message_ids", []))
            success = 0
            attempted = 0
            
            # Forward to channels if selected
            if destinations.get("channels"):
                for dest_channel_id in list(DESTINATION_CHANNELS.keys()):
                    if not await ensure_destination_access(client, dest_channel_id):
                        continue
                    attempted += 1
                    if from_chat_id is not None and message_ids:
                        ok = await safe_forward_messages(client, from_chat_id, message_ids, dest_channel_id)
                    else:
                        ok = False
                    if ok:
                        success += 1
                    await asyncio.sleep(0.05)
            
            # Send to Website API (only if selected)
            api_status = ""
            if destinations.get("website") and ENABLE_WEBSITE_API and from_chat_id is not None and message_ids:
                try:
                    messages = []
                    for msg_id in message_ids:
                        try:
                            msg = await client.get_messages(from_chat_id, msg_id)
                            if msg:
                                messages.append(msg)
                        except Exception:
                            continue
                    
                    if messages:
                        post_text = ""
                        post_entities = None
                        for msg in messages:
                            if msg.text:
                                post_text = msg.text
                                post_entities = msg.entities
                                break
                            elif msg.caption:
                                post_text = msg.caption
                                post_entities = msg.caption_entities
                                break
                        
                        # Split by —— for bilingual content (kr | uz)
                        parts = re.split(r'\n\s*——\s*\n', post_text, maxsplit=1)
                        
                        lang_data: Dict[str, Dict[str, str]] = {}
                        
                        def extract_title_and_body(text_part: str, entities_list) -> Dict[str, str]:
                            """Extract title and formatted body from a text part."""
                            lines = text_part.strip().split("\n")
                            t = ""
                            
                            for line in lines:
                                s = line.strip()
                                if not s:
                                    continue
                                words_in_line = s.split()
                                if words_in_line and all(w.startswith("#") for w in words_in_line):
                                    continue
                                clean = re.sub(r'#\w+', '', s).strip()
                                if not clean:
                                    continue
                                t = clean[:200]
                                break
                            
                            if not t:
                                t = "Yangilik"
                            
                            # Build HTML body
                            html_b = telegram_to_html(text_part, entities_list)
                            html_b = re.sub(r'#\w+', '', html_b)
                            # Remove social media links block at the end
                            html_b = re.sub(r'(<br>)?\s*(Kanalǵa aǵza bolıw|Kanalga a\'zo bo\'lish).*$', '', html_b, flags=re.DOTALL | re.IGNORECASE)
                            html_b = re.sub(r'(<br>)?\s*(Website|Telegram|Instagram|Facebook|YouTube)\s*\|?\s*\(?\s*https?://[^\s)<>]+\)?\s*', '', html_b, flags=re.IGNORECASE)
                            html_b = re.sub(r'(<br>){3,}', '<br><br>', html_b)
                            html_b = html_b.strip()
                            
                            if not html_b:
                                html_b = format_text_for_api(text_part)
                            
                            return {"title": t, "body": html_b, "short_description": t[:150]}
                        
                        if len(parts) == 2:
                            # Bilingual: first part = kr, second = uz
                            kr_part = parts[0]
                            uz_part = parts[1]
                            
                            lang_data["kr"] = extract_title_and_body(kr_part, post_entities)
                            lang_data["uz"] = extract_title_and_body(uz_part, post_entities)
                            # ru and en fallback to uz
                            lang_data["ru"] = lang_data["uz"]
                            lang_data["en"] = lang_data["uz"]
                            
                            logger.info(f"📝 Bilingual post: KR title='{lang_data['kr']['title']}', UZ title='{lang_data['uz']['title']}'")
                        else:
                            # Single language - use for all
                            single = extract_title_and_body(post_text, post_entities)
                            for lang in ["uz", "ru", "en", "kr"]:
                                lang_data[lang] = single
                        
                        image_paths: List[str] = []
                        for msg in messages:
                            if msg.photo:
                                path = await download_media_to_temp(client, msg)
                                if path:
                                    image_paths.append(path)
                        
                        if lang_data.get("uz", {}).get("title") or image_paths:
                            api_ok, api_msg = await send_to_website_api(lang_data, image_paths)
                            if api_ok:
                                api_status = "🌐 Sayt: ✅ Yuborildi"
                            else:
                                api_status = f"🌐 Sayt: ❌ {api_msg}"
                except Exception as e:
                    logger.error(f"Website API error: {e}")
                    api_status = "🌐 Sayt: ❌ Xatolik"
            
            entry["status"] = "done"
            
            summary_parts = ["✅ Yuborish yakunlandi."]
            if destinations.get("channels"):
                summary_parts.append(f"📢 Kanallar: {success}/{attempted} ta")
            else:
                summary_parts.append("📢 Kanallar: o'tkazib yuborildi")
            
            if destinations.get("website"):
                if api_status:
                    summary_parts.append(api_status)
                else:
                    summary_parts.append("🌐 Sayt: o'tkazib yuborildi")
            else:
                summary_parts.append("🌐 Sayt: o'tkazib yuborildi")
            
            summary = "\n".join(summary_parts)
            try:
                await callback_query.message.edit_text(summary)
            except Exception:
                pass
            for admin_id, msg_id in list(entry.get("admin_message_ids", {}).items()):
                if admin_id == callback_query.from_user.id:
                    continue
                try:
                    await client.edit_message_text(admin_id, msg_id, summary)
                except Exception:
                    continue
            PENDING_FORWARDS.pop(token, None)
            return

        if data.startswith("add_channel_"):
            if user_id not in CHECK_CHANNEL_STATE:
                await callback_query.answer("❌ Kanal ma'lumotlari topilmadi. Qayta urinib ko'ring.")
                return
            chat_id = int(data.split("_")[2])
            if chat_id in DESTINATION_CHANNELS:
                await callback_query.answer("❌ Bu kanal allaqachon ro'yxatda mavjud!")
                return
            channel_info = CHECK_CHANNEL_STATE[user_id]
            DESTINATION_CHANNELS[chat_id] = channel_info.get("title") or str(chat_id)
            save_channels(DESTINATION_CHANNELS)
            await callback_query.message.edit_text(
                "✅ Kanal muvaffaqiyatli qo'shildi:\n"
                f"• Nomi: {DESTINATION_CHANNELS[chat_id]}\n"
                f"• ID: {chat_id}"
            )
            CHECK_CHANNEL_STATE.pop(user_id, None)
            await callback_query.answer()
            return

        if data.startswith("remove_channel_"):
            chat_id = int(data.split("_")[2])
            if chat_id not in DESTINATION_CHANNELS:
                await callback_query.answer("❌ Bu kanal allaqachon o'chirilgan!")
                return
            channel_name = DESTINATION_CHANNELS.pop(chat_id)
            save_channels(DESTINATION_CHANNELS)
            await callback_query.message.edit_text(
                "✅ Kanal muvaffaqiyatli o'chirildi:\n"
                f"• Nomi: {channel_name}\n"
                    f"• ID: {chat_id}"
            )
            REMOVE_CHANNEL_STATE.pop(user_id, None)
            await callback_query.answer()
            return

        if data == "cancel_add":
            CHECK_CHANNEL_STATE.pop(user_id, None)
            await callback_query.message.edit_text("❌ Kanal qo'shish bekor qilindi.")
            await callback_query.answer()
            return

        if data == "cancel_remove":
            REMOVE_CHANNEL_STATE.pop(user_id, None)
            await callback_query.message.edit_text("❌ Kanalni o'chirish bekor qilindi.")
            await callback_query.answer()
            return

    except Exception as e:
        await callback_query.answer("Xatolik yuz berdi")
        logger.error(f"Callback error: {e}")


# Forwarding handlers
@app.on_message(filters.chat(SOURCE_CHANNEL))
async def handle_message(client: Client, message: Message) -> None:
    if message.media_group_id:
        media_group_id = str(message.media_group_id)
        if media_group_id not in media_groups:
                media_groups[media_group_id] = []
        media_groups[media_group_id].append(message)

        # debounce timer per media group
        if media_group_id in media_group_timers:
            media_group_timers[media_group_id].cancel()
        task = asyncio.create_task(process_media_group(media_group_id))
        media_group_timers[media_group_id] = task
        return

    # Single message: request admin approval
    from_chat = message.chat.id
    token = _make_forward_token_for_message(from_chat, message.id)
    if token in PENDING_FORWARDS:
        return
    try:
        chat = await client.get_chat(from_chat)
        source_title = chat.title or str(from_chat)
    except Exception:
        source_title = str(from_chat)
    PENDING_FORWARDS[token] = {
        "type": "msg",
        "from_chat_id": from_chat,
        "message_ids": [message.id],
        "status": "waiting",
        "admin_message_ids": {},
    }
    await request_forward_approval(client, token, source_title, len(DESTINATION_CHANNELS))


async def process_media_group(media_group_id: str) -> None:
    # wait for all parts of the album
    await asyncio.sleep(1.5)
    messages = media_groups.get(media_group_id)
    if not messages:
        return
    messages.sort(key=lambda m: m.id)
    from_chat = messages[0].chat.id
    message_ids = [m.id for m in messages]
    # Request admin approval for media group
    token = _make_forward_token_for_group(media_group_id)
    try:
        chat = await app.get_chat(from_chat)
        source_title = chat.title or str(from_chat)
    except Exception:
        source_title = str(from_chat)
    PENDING_FORWARDS[token] = {
        "type": "mg",
        "from_chat_id": from_chat,
        "message_ids": message_ids,
        "status": "waiting",
        "admin_message_ids": {},
    }
    await request_forward_approval(app, token, source_title, len(DESTINATION_CHANNELS))
    # cleanup
    media_groups.pop(media_group_id, None)
    if media_group_id in media_group_timers:
        media_group_timers.pop(media_group_id, None)


if __name__ == "__main__":
    if not SOURCE_CHANNEL:
        logger.error("SOURCE_CHANNEL is not set (env or channels.json). Set it before running.")
    else:
        logger.info("Userbot starting...")
        logger.info(f"Manba kanal: {SOURCE_CHANNEL}")
        logger.info(f"Maqsad kanallar: {DESTINATION_CHANNELS}")
        app.run()


