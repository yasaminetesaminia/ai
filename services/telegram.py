import requests

import config

API_BASE = "https://api.telegram.org"


def send_message(text: str) -> dict:
    """Send a plain-text message to the clinic's Telegram chat."""
    url = f"{API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def send_document(
    filename: str,
    content: str | bytes,
    caption: str | None = None,
    mime_type: str = "text/plain; charset=utf-8",
) -> dict:
    """Upload an in-memory document (text or binary) to the Telegram chat."""
    url = f"{API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendDocument"
    payload = content.encode("utf-8") if isinstance(content, str) else content
    files = {"document": (filename, payload, mime_type)}
    data = {"chat_id": config.TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption
    response = requests.post(url, data=data, files=files, timeout=60)
    response.raise_for_status()
    return response.json()
