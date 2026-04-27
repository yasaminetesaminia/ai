import logging

import requests
import config

logger = logging.getLogger(__name__)


GRAPH_VERSION = "v21.0"


def send_message(to_igsid: str, text: str):
    """Send a text message via the Instagram Graph API.

    Failures are logged at WARNING (not ERROR) and swallowed so a Meta
    token expiry or transient outage doesn't crash the webhook handler.
    """
    url = f"https://graph.instagram.com/{GRAPH_VERSION}/me/messages"
    headers = {
        "Authorization": f"Bearer {config.INSTAGRAM_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "recipient": {"id": to_igsid},
        "message": {"text": text},
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 401:
            logger.warning(
                "Instagram token expired (401) — refresh INSTAGRAM_TOKEN in .env. "
                "Skipping send to %s.", to_igsid,
            )
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("Instagram send to %s failed: %s", to_igsid, e)
        return None


def download_media(media_url: str) -> bytes:
    """Download a media file from a direct Instagram CDN URL.

    Instagram webhooks deliver attachment.payload.url already resolved (unlike WhatsApp,
    which only gives a media_id that needs a separate lookup).
    """
    response = requests.get(media_url, timeout=30)
    response.raise_for_status()
    return response.content


def parse_incoming(payload: dict) -> dict | None:
    """Parse incoming Instagram DM webhook payload.

    Returns dict with keys: from_igsid, message_type, text, media_url
    or None if not a valid user message.
    """
    try:
        entry = payload["entry"][0]

        if "messaging" not in entry:
            return None

        message_event = entry["messaging"][0]

        # Skip echoes (messages we sent ourselves)
        if message_event.get("message", {}).get("is_echo"):
            return None

        sender_id = message_event["sender"]["id"]
        recipient_id = message_event["recipient"]["id"]

        # Ignore events the business account sent to itself
        if sender_id == config.INSTAGRAM_ACCOUNT_ID:
            return None

        message = message_event.get("message")
        if not message:
            return None

        # Carry message_id + timestamp (Meta sends both) so the webhook
        # handler can dedupe retries and skip stale deliveries.
        result = {
            "from_igsid": sender_id,
            "recipient_id": recipient_id,
            "message_type": "text",
            "text": None,
            "media_url": None,
            "message_id": message.get("mid", ""),
            "timestamp": int(message_event.get("timestamp", 0) or 0) // 1000,  # IG sends ms
        }

        if "text" in message:
            result["text"] = message["text"]
            return result

        attachments = message.get("attachments") or []
        if attachments:
            att = attachments[0]
            att_type = att.get("type")
            media_url = att.get("payload", {}).get("url")
            if att_type == "audio" and media_url:
                result["message_type"] = "audio"
                result["media_url"] = media_url
                return result
            # Other attachment types (image/video/etc.) — not supported for booking flow
            result["message_type"] = att_type or "unsupported"
            return result

        return None
    except (KeyError, IndexError):
        return None
