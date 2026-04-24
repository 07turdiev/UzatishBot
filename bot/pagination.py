import io
import math
from typing import List, Optional, Tuple

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import PAGE_SIZE_REMOVE, PAGE_SIZE_CHANNELS, PAGE_SIZE_DIAGNOSE
from bot.channels import DESTINATION_CHANNELS, get_destinations_sorted


def create_remove_list_markup(page: int = 0, per_page: int = PAGE_SIZE_REMOVE) -> Optional[InlineKeyboardMarkup]:
    items = get_destinations_sorted(DESTINATION_CHANNELS)
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
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_remove")])
    return InlineKeyboardMarkup(buttons)


async def make_channels_page(page: int = 0, per_page: int = PAGE_SIZE_CHANNELS) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    items = get_destinations_sorted(DESTINATION_CHANNELS)
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, total)
    lines = [f"📄 Kanallar ro'yxati ({total} ta):\n"]
    for idx, (cid, title) in enumerate(items[start:end], start=start + 1):
        lines.append(f"{idx}. {title}\n  ID: {cid}")
    text = "\n".join(lines)
    nav_row: List[InlineKeyboardButton] = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"channels_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"channels_page_{page + 1}"))
    markup = InlineKeyboardMarkup([nav_row]) if nav_row else None
    return text, markup


async def make_diagnose_page(client: Client, page: int = 0, per_page: int = PAGE_SIZE_DIAGNOSE) -> Tuple[str, InlineKeyboardMarkup]:
    items = get_destinations_sorted(DESTINATION_CHANNELS)
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


async def diagnose_full_report(client: Client) -> bytes:
    items = get_destinations_sorted(DESTINATION_CHANNELS)
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
