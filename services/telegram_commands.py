"""Command handler for Telegram messages sent by the clinic receptionist.

Only messages from `config.TELEGRAM_CHAT_ID` are accepted — everything else
is silently ignored, so anyone guessing the webhook URL can't poke the bot.

Supported commands:
  /today                        — list today's appointments
  /tomorrow                     — list tomorrow's appointments
  /week                         — appointments for the next 7 days
  /find <query>                 — find appointments by client name or phone substring
  /cancel <phone>               — cancel the caller's next appointment (by phone)
  /waitlist                     — list current waitlist entries
  /package_catalog              — show all packages for sale
  /package_list <phone>         — show a client's active packages
  /package_remove <id>          — remove/refund a package
  /help                         — show this command list

Package registration happens in the "Packages" tab of the appointments
Google Sheet — the receptionist fills Phone/Name/Code/Language and the
sync job creates the package within 3 minutes.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from services import google_calendar, google_sheets, packages, waitlist, telegram
from services_config import PACKAGES, get_packages_catalog_text

logger = logging.getLogger(__name__)

_tz = ZoneInfo(config.BUSINESS_TIMEZONE)


def _format_appts(appts: list[dict], header: str) -> str:
    if not appts:
        return f"{header}\n(no appointments)"
    lines = [header]
    for a in appts:
        name = a.get("client_name") or "-"
        mobile = a.get("mobile") or a.get("phone") or "-"
        summary = a.get("summary") or "-"
        lines.append(
            f"• {a['date']} {a['time']} — {name} ({mobile})\n   {summary}"
        )
    return "\n".join(lines)


def _today_range() -> tuple[datetime, datetime]:
    now = datetime.now(_tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _hours_ahead(hours: int) -> list[dict]:
    return google_calendar.get_upcoming_appointments(hours_ahead=hours)


def _cmd_today() -> str:
    start, end = _today_range()
    appts = _hours_ahead(24)
    today = start.strftime("%Y-%m-%d")
    todays = [a for a in appts if a["date"] == today]
    return _format_appts(todays, f"📅 Today ({today}):")


def _cmd_tomorrow() -> str:
    start, _ = _today_range()
    target = (start + timedelta(days=1)).strftime("%Y-%m-%d")
    appts = _hours_ahead(48)
    tomorrows = [a for a in appts if a["date"] == target]
    return _format_appts(tomorrows, f"📅 Tomorrow ({target}):")


def _cmd_week() -> str:
    appts = _hours_ahead(24 * 7)
    return _format_appts(appts, "📅 Next 7 days:")


def _cmd_find(query: str) -> str:
    if not query:
        return "Usage: /find <name-or-phone>"
    q = query.strip().lower()
    appts = _hours_ahead(24 * 60)
    matched = [
        a for a in appts
        if q in (a.get("client_name") or "").lower()
        or q in (a.get("mobile") or "").lower()
        or q in (a.get("phone") or "").lower()
    ]
    return _format_appts(matched, f"🔎 Matches for '{query}':")


def _cmd_cancel(arg: str) -> str:
    phone = (arg or "").strip()
    if not phone:
        return "Usage: /cancel <phone-or-id>"
    cancelled = google_calendar.cancel_appointment(phone)
    if not cancelled:
        return f"No upcoming appointment found for {phone}."
    google_sheets.delete_appointment(
        phone=cancelled["mobile"], date=cancelled["date"], time=cancelled["time"]
    )
    return (
        f"✅ Cancelled: {cancelled.get('client_name') or phone} "
        f"on {cancelled['date']} {cancelled['time']}."
    )


def _cmd_waitlist() -> str:
    entries = waitlist.list_all()
    if not entries:
        return "Waitlist is empty."
    lines = [f"📋 Waitlist ({len(entries)}):"]
    for e in entries:
        lines.append(
            f"• {e['client_name']} ({e['client_mobile']}) — "
            f"{e['department']} / {e['sub_service']} @ "
            f"{e['desired_date']} {e['desired_time']}"
        )
    return "\n".join(lines)


def _cmd_help() -> str:
    return (
        "Commands:\n"
        "/today — today's appointments\n"
        "/tomorrow — tomorrow's appointments\n"
        "/week — next 7 days\n"
        "/find <name-or-phone> — search appointments\n"
        "/cancel <phone> — cancel client's next appointment\n"
        "/waitlist — current waitlist entries\n"
        "/package_catalog — show packages for sale\n"
        "/package_list <phone> — show client's active packages\n"
        "/package_remove <package_id> — remove/refund a package\n"
        "/help — this message\n\n"
        "To register a paid package: open the appointments Google Sheet → "
        "Packages tab → add a row with Phone, Name, Package Code, Language. "
        "The system fills in the rest within 3 minutes."
    )


def _cmd_package_catalog(_arg: str) -> str:
    return "📦 Package catalog:\n" + get_packages_catalog_text(language="en")


def _cmd_package_list(arg: str) -> str:
    phone = (arg or "").strip()
    if not phone:
        return "Usage: /package_list <phone>"
    active = packages.get_active_packages(phone)
    if not active:
        return f"No active packages for {phone}."
    lines = [f"📦 Packages for {phone}:"]
    for p in active:
        name = PACKAGES.get(p["package_code"], {}).get("name_en", p["package_code"])
        remaining = packages.sessions_remaining(p)
        lines.append(
            f"• {name} — {remaining}/{p['total_sessions']} left, "
            f"expires {p['expires_at']}\n   id: {p['id']}"
        )
    return "\n".join(lines)


def _cmd_package_remove(arg: str) -> str:
    pid = (arg or "").strip()
    if not pid:
        return "Usage: /package_remove <package_id>"
    if packages.remove_package(pid):
        return f"✅ Removed package {pid}."
    return f"Package {pid} not found."


_HANDLERS = {
    "/today": lambda _: _cmd_today(),
    "/tomorrow": lambda _: _cmd_tomorrow(),
    "/week": lambda _: _cmd_week(),
    "/find": _cmd_find,
    "/cancel": _cmd_cancel,
    "/waitlist": lambda _: _cmd_waitlist(),
    "/package_catalog": _cmd_package_catalog,
    "/package_list": _cmd_package_list,
    "/package_remove": _cmd_package_remove,
    "/help": lambda _: _cmd_help(),
    "/start": lambda _: _cmd_help(),
}


def handle_update(payload: dict) -> None:
    """Entry point for Telegram webhook updates."""
    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if chat_id != str(config.TELEGRAM_CHAT_ID):
        # Reject messages from anywhere except the configured clinic chat.
        logger.warning(f"Ignored Telegram message from unauthorized chat {chat_id}")
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    parts = text.split(None, 1)
    command = parts[0].lower()
    # Strip bot-name suffix like "/today@NooraBot"
    command = command.split("@", 1)[0]
    arg = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(command)
    if not handler:
        telegram.send_message(f"Unknown command {command}. Send /help for the list.")
        return

    try:
        reply = handler(arg)
    except Exception as e:
        logger.error(f"Telegram command {command} failed: {e}", exc_info=True)
        reply = f"Command failed: {e}"

    if reply:
        telegram.send_message(reply)
