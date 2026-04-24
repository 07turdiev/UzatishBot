import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import SOURCE_CHANNEL
from bot.client import app
from bot.channels import DESTINATION_CHANNELS
from bot.state import media_groups, media_group_timers, PENDING_FORWARDS
from bot.forwarding import (
    _make_forward_token_for_message,
    _make_forward_token_for_group,
    request_forward_approval,
)


@app.on_message(filters.chat(SOURCE_CHANNEL))
async def handle_message(client: Client, message: Message) -> None:
    if message.media_group_id:
        media_group_id = str(message.media_group_id)
        if media_group_id not in media_groups:
            media_groups[media_group_id] = []
        media_groups[media_group_id].append(message)

        if media_group_id in media_group_timers:
            media_group_timers[media_group_id].cancel()
        task = asyncio.create_task(_process_media_group(media_group_id))
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


async def _process_media_group(media_group_id: str) -> None:
    await asyncio.sleep(1.5)
    messages = media_groups.get(media_group_id)
    if not messages:
        return
    messages.sort(key=lambda m: m.id)
    from_chat = messages[0].chat.id
    message_ids = [m.id for m in messages]
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
    media_groups.pop(media_group_id, None)
    if media_group_id in media_group_timers:
        media_group_timers.pop(media_group_id, None)
