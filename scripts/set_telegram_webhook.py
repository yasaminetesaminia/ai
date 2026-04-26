"""One-off helper to register the bot's Telegram webhook.

Usage:
    python scripts/set_telegram_webhook.py https://slot-employed-deem.ngrok-free.dev

After this, the receptionist can send commands (/today, /week, /cancel, etc.)
directly to the bot in Telegram.
"""

import sys

import requests

import config


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/set_telegram_webhook.py <public-base-url>")
        sys.exit(1)
    base = sys.argv[1].rstrip("/")
    url = f"{base}/telegram/webhook"
    r = requests.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook",
        data={"url": url, "allowed_updates": '["message","edited_message"]'},
        timeout=15,
    )
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
