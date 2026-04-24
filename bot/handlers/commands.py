import io

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import ADMIN_USERS, logger
from bot.client import app
from bot.channels import DESTINATION_CHANNELS
from bot.state import CHECK_CHANNEL_STATE, REMOVE_CHANNEL_STATE, VALID_DEST_CHANNELS, INVALID_DEST_CHANNELS
from bot.pagination import create_remove_list_markup, make_channels_page, make_diagnose_page


@app.on_message(filters.command(["start", "help"]) & filters.private)
async def help_handler(client: Client, message: Message) -> None:
    is_admin = message.from_user and message.from_user.id in ADMIN_USERS
    if not is_admin:
        await message.reply_text("❌ Sizda bu botdan foydalanish huquqi mavjud emas")
        return
    help_text = (
        "👋 Qoraqalpog'iston Respublikasi Madaniyat vazirligi\n\n"
        "📝 Admin buyruqlari:\n"
        "• /check_channel – Yangi kanal qo'shish\n"
        "• /remove_channel – Kanalni o'chirish\n"
        "• /channels – Kanallar ro'yxati\n"
        "• /diagnose – Maqsad kanallarga kirish va ruxsatlarni tekshirish\n"
        "• /clear_cache – Invalid kanallar cache'ini tozalash\n\n"
    )
    await message.reply_text(help_text)


@app.on_message(filters.command("diagnose") & filters.private)
async def diagnose_handler(client: Client, message: Message) -> None:
    if message.from_user and message.from_user.id not in ADMIN_USERS:
        await message.reply_text("❌ Kechirasiz, bu buyruq faqat adminlar uchun.")
        return
    if not DESTINATION_CHANNELS:
        await message.reply_text("ℹ️ Hozircha kanallar yo'q.")
        return
    text, markup = await make_diagnose_page(client, page=0)
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
    text, markup = await make_channels_page(page=0)
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
