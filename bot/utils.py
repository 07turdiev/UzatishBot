import os
import re
import tempfile
from typing import List, Optional, Union
from html import escape as html_escape

from pyrogram.types import Message, MessageEntity
from pyrogram.enums import MessageEntityType
from pyrogram import Client

from bot.config import logger


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


def parse_chat_identifier(raw: str) -> Union[int, str]:
    s = (raw or "").strip()
    if not s:
        return s
    lower = s.lower()
    # t.me links
    if lower.startswith("https://t.me/") or lower.startswith("http://t.me/") or lower.startswith("t.me/"):
        parts = s.split("/")
        try:
            idx = next(i for i, p in enumerate(parts) if p.endswith("t.me") or p.endswith("t.me:"))
        except StopIteration:
            idx = 2
        path = parts[idx + 1:]
        if not path:
            return s
        if path[0] == 'c' and len(path) >= 2 and path[1].lstrip("-+ ").isdigit():
            digits = path[1].lstrip("-+ ")
            return int(f"-100{digits}")
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
        return int(f"-100{digits}")
    return s


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
