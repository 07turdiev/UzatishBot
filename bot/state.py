import os
import json
import asyncio
from typing import Dict, List, Set

from pyrogram.types import Message

from bot.config import logger

# Media group collection (transient — not persisted)
media_groups: Dict[str, List[Message]] = {}
media_group_timers: Dict[str, asyncio.Task] = {}

# User interaction states (transient)
CHECK_CHANNEL_STATE: Dict[int, dict] = {}
REMOVE_CHANNEL_STATE: Dict[int, dict] = {}

# Channel access cache (transient)
VALID_DEST_CHANNELS: Set[int] = set()
INVALID_DEST_CHANNELS: Set[int] = set()

# Pending forward approvals (persisted)
PENDING_FORWARDS: Dict[str, dict] = {}

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state.json")


def save_pending_forwards() -> None:
    """Persist PENDING_FORWARDS to disk so they survive restarts."""
    try:
        serializable = {}
        for token, entry in PENDING_FORWARDS.items():
            if entry.get("status") in ("done", "cancelled"):
                continue
            serializable[token] = {
                "type": entry.get("type"),
                "from_chat_id": entry.get("from_chat_id"),
                "message_ids": entry.get("message_ids", []),
                "status": entry.get("status"),
                "admin_message_ids": {
                    str(k): v for k, v in entry.get("admin_message_ids", {}).items()
                },
                "destinations": entry.get("destinations"),
            }
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"pending_forwards": serializable}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving state: {e}")


def load_pending_forwards() -> None:
    """Restore PENDING_FORWARDS from disk on startup."""
    global PENDING_FORWARDS
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("pending_forwards", {})
        for token, entry in raw.items():
            entry["admin_message_ids"] = {
                int(k): v for k, v in entry.get("admin_message_ids", {}).items()
            }
            PENDING_FORWARDS[token] = entry
        if PENDING_FORWARDS:
            logger.info(f"Restored {len(PENDING_FORWARDS)} pending forward(s) from state.json")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"Error loading state: {e}")


# Restore on import
load_pending_forwards()
