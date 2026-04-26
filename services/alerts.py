"""Centralized error alerts to the clinic's Telegram chat.

Two entry points:
  * `notify(text)` — manually send an admin alert from anywhere in the code.
  * `install_logging_handler()` — attach a logging handler that forwards
    ERROR-level logs (and above) to Telegram, with deduplication so a repeating
    error doesn't spam the chat.

Rate-limit: each unique error signature is sent at most once every
`_DEDUP_WINDOW_SECONDS`. Signatures combine logger name + first line of the
message so the same stacktrace firing in a loop only alerts once.
"""

import logging
import threading
import time
import traceback

import requests

import config

_logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_DEDUP_WINDOW_SECONDS = 300  # 5 minutes between alerts for the same signature
_lock = threading.Lock()
_last_sent: dict[str, float] = {}


def _send(text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"{_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text[:3900],  # Telegram hard limit ~4096
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception:
        # Never let alert failures crash the caller — they're best-effort.
        pass


def notify(text: str, dedup_key: str | None = None) -> None:
    """Send an admin alert to the clinic's Telegram chat.

    If `dedup_key` is provided, suppress repeats within the dedup window.
    """
    if dedup_key:
        now = time.time()
        with _lock:
            last = _last_sent.get(dedup_key, 0)
            if now - last < _DEDUP_WINDOW_SECONDS:
                return
            _last_sent[dedup_key] = now
    _send(text)


_NOISE_LOGGERS = {
    # Werkzeug logs request lifecycle (timeouts, dropped connections,
    # client-side aborts) at ERROR level — these are not real bugs, they're
    # routine traffic noise from a public-facing tunnel. Filter them out so
    # the clinic's Telegram only fires for actual code-level errors.
    "werkzeug",
    "urllib3.connectionpool",
    "httpx",
}

_NOISE_PHRASES = [
    "request timed out",
    "broken pipe",
    "connection reset",
    "client disconnected",
    "ssl: wrong_version_number",
    "ssl: decryption_failed",
    "the read operation timed out",
]


class _TelegramErrorHandler(logging.Handler):
    """Logging handler that forwards ERROR-and-above records to Telegram,
    after filtering out routine HTTP/network noise.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Skip records from loggers that produce mostly noise.
            if record.name in _NOISE_LOGGERS:
                return

            msg = record.getMessage()
            msg_lower = msg.lower()
            if any(phrase in msg_lower for phrase in _NOISE_PHRASES):
                return

            first_line = msg.split("\n", 1)[0][:200]
            signature = f"{record.name}|{record.levelname}|{first_line}"

            body = f"🚨 {config.BUSINESS_NAME} — {record.levelname}\n"
            body += f"[{record.name}] {msg}"
            if record.exc_info:
                body += "\n\n" + "".join(traceback.format_exception(*record.exc_info))

            notify(body, dedup_key=signature)
        except Exception:
            # Handlers must never raise — that would break logging for all.
            pass


_installed = False


def install_logging_handler() -> None:
    """Attach the Telegram handler to the root logger. Idempotent."""
    global _installed
    if _installed:
        return
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        _logger.info("Telegram not configured; error alerts disabled.")
        return

    handler = _TelegramErrorHandler(level=logging.ERROR)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    _installed = True
    _logger.info("Telegram error-alert handler installed.")
