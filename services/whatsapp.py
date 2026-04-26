import logging

import requests
import config

logger = logging.getLogger(__name__)


def send_message(to_phone: str, text: str):
    """Send a text message via WhatsApp Business API.

    Failures are logged at WARNING (not ERROR) and swallowed — Meta
    tokens expire periodically and WhatsApp/IG outages happen; we don't
    want a transient delivery failure to crash the whole webhook handler
    or page anyone with an alert. The clinic notices via the dashboard
    if conversations stop responding.
    """
    url = f"https://graph.facebook.com/v21.0/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 401:
            logger.warning(
                "WhatsApp token expired (401) — refresh WHATSAPP_TOKEN in .env. "
                "Skipping send to %s.", to_phone,
            )
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("WhatsApp send to %s failed: %s", to_phone, e)
        return None


def download_media(media_id: str) -> bytes:
    """Download a media file (voice message) from WhatsApp."""
    # Step 1: Get media URL
    url = f"https://graph.facebook.com/v21.0/{media_id}"
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    media_url = response.json()["url"]

    # Step 2: Download the actual file
    response = requests.get(media_url, headers=headers)
    response.raise_for_status()
    return response.content


def parse_incoming(payload: dict) -> dict | None:
    """Parse incoming WhatsApp webhook payload.

    Returns dict with keys: from_phone, message_type, text, media_id
    or None if not a valid user message.
    """
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return None

        message = value["messages"][0]
        from_phone = message["from"]
        message_type = message["type"]

        result = {
            "from_phone": from_phone,
            "message_type": message_type,
            "text": None,
            "media_id": None,
        }

        if message_type == "text":
            result["text"] = message["text"]["body"]
        elif message_type == "audio":
            result["media_id"] = message["audio"]["id"]

        return result
    except (KeyError, IndexError):
        return None
