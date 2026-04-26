"""Daily check that the WhatsApp and Instagram access tokens still have
some runway left, alerting the clinic before they expire.

Uses Meta's `/debug_token` endpoint, which needs an app access token
(`{app_id}|{app_secret}`). If `META_APP_ID` or `META_APP_SECRET` is not
configured, we skip silently so the scheduler doesn't crash.
"""

import logging
from datetime import datetime, timezone

import requests

import config
from services import alerts

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v21.0"
_WARNING_THRESHOLD_DAYS = 7


def _app_token() -> str | None:
    if not (config.META_APP_ID and config.META_APP_SECRET):
        return None
    return f"{config.META_APP_ID}|{config.META_APP_SECRET}"


def _check(label: str, token: str | None, app_token: str) -> None:
    if not token:
        return
    try:
        r = requests.get(
            f"{_GRAPH_BASE}/debug_token",
            params={"input_token": token, "access_token": app_token},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
    except Exception as e:
        logger.warning(f"{label} debug_token call failed: {e}")
        return

    if not data.get("is_valid", True):
        alerts.notify(
            f"❌ {label} access token is INVALID. "
            f"Refresh it immediately or the bot will stop responding.",
            dedup_key=f"token_invalid_{label}",
        )
        return

    expires_at = data.get("expires_at")
    # `0` means the token never expires (system-user tokens). Skip those.
    if not expires_at:
        return

    expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    remaining = (expiry - datetime.now(timezone.utc)).days
    if remaining <= _WARNING_THRESHOLD_DAYS:
        alerts.notify(
            f"⚠️ {label} access token expires in {remaining} day(s) "
            f"(on {expiry.date().isoformat()}). Refresh it soon.",
            dedup_key=f"token_expiring_{label}_{expiry.date().isoformat()}",
        )


def check_tokens() -> None:
    """Scheduler entry point. Checks WhatsApp + Instagram tokens."""
    app_token = _app_token()
    if not app_token:
        logger.debug("META_APP_ID / META_APP_SECRET not set; skipping token check.")
        return
    _check("WhatsApp", config.WHATSAPP_TOKEN, app_token)
    _check("Instagram", config.INSTAGRAM_TOKEN, app_token)
