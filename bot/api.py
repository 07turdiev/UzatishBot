import os
from typing import Dict, List, Tuple

import aiohttp

from bot.config import WEBSITE_API_URL, WEBSITE_API_KEY, logger


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

            for lang in ["uz", "ru", "en", "kr"]:
                ld = lang_data.get(lang, lang_data.get("uz", {}))
                data.add_field(f"title_{lang}", ld.get("title", "Yangilik"))
                data.add_field(f"body_{lang}", ld.get("body", ""))
                data.add_field(f"short_description_{lang}", ld.get("short_description", ld.get("title", "")[:150]))

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
