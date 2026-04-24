from typing import Optional

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.client import app
from bot.channels import DESTINATION_CHANNELS
from bot.state import CHECK_CHANNEL_STATE, REMOVE_CHANNEL_STATE
from bot.utils import parse_chat_identifier


@app.on_message(filters.private & filters.text)
async def handle_text_messages(client: Client, message: Message) -> None:
    if message.text.startswith('/'):
        return
    user_id = message.from_user.id if message.from_user else 0
    text = message.text.strip()

    if user_id in CHECK_CHANNEL_STATE and CHECK_CHANNEL_STATE[user_id].get("waiting_for_username"):
        await _process_channel_check(client, message, user_id, text)
        return

    if user_id in REMOVE_CHANNEL_STATE and REMOVE_CHANNEL_STATE[user_id].get("waiting_for_channel"):
        await _process_channel_remove(client, message, user_id, text)
        return


async def _process_channel_check(client: Client, message: Message, user_id: int, channel: str) -> None:
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


async def _process_channel_remove(client: Client, message: Message, user_id: int, channel: str) -> None:
    try:
        chat_id: Optional[int] = None
        if channel.replace('-', '').isdigit():
            try:
                chat_id = int(channel)
            except ValueError:
                chat_id = None
        else:
            try:
                chat = await client.get_chat(channel)
                chat_id = int(chat.id)
            except Exception:
                chat_id = None

        if chat_id is not None and chat_id not in DESTINATION_CHANNELS:
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
