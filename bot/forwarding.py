import asyncio
from typing import List

from pyrogram import Client
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import FloodWait, RPCError

from bot.config import ADMIN_USERS, logger
from bot.state import PENDING_FORWARDS, VALID_DEST_CHANNELS, INVALID_DEST_CHANNELS, save_pending_forwards


def _make_forward_token_for_message(chat_id: int, message_id: int) -> str:
    return f"msg:{chat_id}:{message_id}"


def _make_forward_token_for_group(media_group_id: str) -> str:
    return f"mg:{media_group_id}"


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
    save_pending_forwards()
