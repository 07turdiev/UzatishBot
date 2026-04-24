import io
import re
import asyncio
from typing import Dict, List

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from bot.config import ADMIN_USERS, ENABLE_WEBSITE_API, logger
from bot.client import app
from bot.channels import DESTINATION_CHANNELS, save_channels
from bot.state import (
    CHECK_CHANNEL_STATE,
    REMOVE_CHANNEL_STATE,
    PENDING_FORWARDS,
    save_pending_forwards,
)
from bot.forwarding import safe_forward_messages, ensure_destination_access
from bot.api import send_to_website_api
from bot.utils import telegram_to_html, format_text_for_api, download_media_to_temp
from bot.pagination import (
    create_remove_list_markup,
    make_channels_page,
    make_diagnose_page,
    diagnose_full_report,
)


@app.on_callback_query()
async def handle_channel_callback(client: Client, callback_query: CallbackQuery) -> None:
    user_id = callback_query.from_user.id
    data = callback_query.data or ""

    try:
        if data == "noop":
            await callback_query.answer()
            return

        if data.startswith("remove_list_page_"):
            await _handle_remove_list_page(client, callback_query, user_id, data)
            return

        if data.startswith("channels_page_"):
            await _handle_channels_page(client, callback_query, data)
            return

        if data.startswith("diagnose_page_"):
            await _handle_diagnose_page(client, callback_query, data)
            return

        if data == "diagnose_download":
            await _handle_diagnose_download(client, callback_query)
            return

        if data.startswith("fw_yes:") or data.startswith("fw_no:"):
            await _handle_forward_decision(client, callback_query, data)
            return

        if data.startswith("dest_ch:") or data.startswith("dest_web:"):
            await _handle_dest_toggle(client, callback_query, data)
            return

        if data.startswith("dest_confirm:"):
            await _handle_dest_confirm(client, callback_query, data)
            return

        if data.startswith("add_channel_"):
            await _handle_add_channel(client, callback_query, user_id, data)
            return

        if data.startswith("remove_channel_"):
            await _handle_remove_channel(client, callback_query, user_id, data)
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


async def _handle_remove_list_page(client: Client, callback_query: CallbackQuery, user_id: int, data: str) -> None:
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


async def _handle_channels_page(client: Client, callback_query: CallbackQuery, data: str) -> None:
    if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
        await callback_query.answer("Ruxsat yo'q")
        return
    try:
        new_page = int(data.split("_")[-1])
    except ValueError:
        await callback_query.answer("Noto'g'ri sahifa")
        return
    text, markup = await make_channels_page(page=new_page)
    try:
        await callback_query.message.edit_text(text, reply_markup=markup if markup else None)
    except Exception:
        pass
    await callback_query.answer()


async def _handle_diagnose_page(client: Client, callback_query: CallbackQuery, data: str) -> None:
    if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
        await callback_query.answer("Ruxsat yo'q")
        return
    try:
        new_page = int(data.split("_")[-1])
    except ValueError:
        await callback_query.answer("Noto'g'ri sahifa")
        return
    text, markup = await make_diagnose_page(client, page=new_page)
    try:
        await callback_query.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass
    await callback_query.answer()


async def _handle_diagnose_download(client: Client, callback_query: CallbackQuery) -> None:
    if callback_query.from_user and callback_query.from_user.id not in ADMIN_USERS:
        await callback_query.answer("Ruxsat yo'q")
        return
    await callback_query.answer("Yuklanmoqda...")
    content = await diagnose_full_report(client)
    doc = io.BytesIO(content)
    doc.name = "diagnose_report.txt"
    try:
        await client.send_document(callback_query.from_user.id, doc, caption="Diagnose to'liq hisobot")
    except Exception:
        pass


async def _handle_forward_decision(client: Client, callback_query: CallbackQuery, data: str) -> None:
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
        save_pending_forwards()
        await callback_query.answer("Bekor qilindi")
        for admin_id, msg_id in list(entry.get("admin_message_ids", {}).items()):
            try:
                await client.edit_message_text(admin_id, msg_id, "❌ Yuborish bekor qilindi.")
            except Exception:
                continue
        return

    entry["status"] = "selecting"
    entry["destinations"] = {"channels": True, "website": True}
    save_pending_forwards()
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


async def _handle_dest_toggle(client: Client, callback_query: CallbackQuery, data: str) -> None:
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


async def _handle_dest_confirm(client: Client, callback_query: CallbackQuery, data: str) -> None:
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
        api_status = await _send_to_website(client, from_chat_id, message_ids)

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
    save_pending_forwards()


async def _send_to_website(client: Client, from_chat_id: int, message_ids: List[int]) -> str:
    """Handle website API sending logic. Returns status string."""
    try:
        messages = []
        for msg_id in message_ids:
            try:
                msg = await client.get_messages(from_chat_id, msg_id)
                if msg:
                    messages.append(msg)
            except Exception:
                continue

        if not messages:
            return ""

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

            html_b = telegram_to_html(text_part, entities_list)
            html_b = re.sub(r'#\w+', '', html_b)
            html_b = re.sub(r'(<br>)?\s*(Kanalǵa aǵza bolıw|Kanalga a\'zo bo\'lish).*$', '', html_b, flags=re.DOTALL | re.IGNORECASE)
            html_b = re.sub(r'(<br>)?\s*(Website|Telegram|Instagram|Facebook|YouTube)\s*\|?\s*\(?\s*https?://[^\s)<>]+\)?\s*', '', html_b, flags=re.IGNORECASE)
            html_b = re.sub(r'(<br>){3,}', '<br><br>', html_b)
            html_b = html_b.strip()

            if not html_b:
                html_b = format_text_for_api(text_part)

            return {"title": t, "body": html_b, "short_description": t[:150]}

        if len(parts) == 2:
            kr_part = parts[0]
            uz_part = parts[1]
            lang_data["kr"] = extract_title_and_body(kr_part, post_entities)
            lang_data["uz"] = extract_title_and_body(uz_part, post_entities)
            lang_data["ru"] = lang_data["uz"]
            lang_data["en"] = lang_data["uz"]
            logger.info(f"📝 Bilingual post: KR title='{lang_data['kr']['title']}', UZ title='{lang_data['uz']['title']}'")
        else:
            single = extract_title_and_body(post_text, post_entities)
            for lang in ["uz", "ru", "en", "kr"]:
                lang_data[lang] = single

        image_paths: List[str] = []
        for msg in messages:
            if msg.photo:
                path = await download_media_to_temp(client, msg)
                if path:
                    image_paths.append(path)

        if not image_paths:
            return "🌐 Sayt: ⚠️ Rasm yo'q (sayt rasm talab qiladi)"

        if lang_data.get("uz", {}).get("title"):
            api_ok, api_msg = await send_to_website_api(lang_data, image_paths)
            if api_ok:
                return "🌐 Sayt: ✅ Yuborildi"
            else:
                return f"🌐 Sayt: ❌ {api_msg}"
    except Exception as e:
        logger.error(f"Website API error: {e}")
        return "🌐 Sayt: ❌ Xatolik"

    return ""


async def _handle_add_channel(client: Client, callback_query: CallbackQuery, user_id: int, data: str) -> None:
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


async def _handle_remove_channel(client: Client, callback_query: CallbackQuery, user_id: int, data: str) -> None:
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
