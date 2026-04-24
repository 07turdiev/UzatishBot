import json
from typing import Dict, List, Tuple

from bot.config import CHANNELS_FILE, logger


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


def get_destinations_sorted(destination_channels: Dict[int, str]) -> List[Tuple[int, str]]:
    """Stable order by title then id."""
    return sorted(destination_channels.items(), key=lambda kv: (kv[1] or "", kv[0]))


# Loaded at import time
DESTINATION_CHANNELS = load_channels()
