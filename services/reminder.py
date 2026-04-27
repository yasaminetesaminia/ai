import json
import logging
import os

from services import google_calendar, whatsapp
import config

logger = logging.getLogger(__name__)

# Persist reminded-event ids to disk so redeploys don't re-send.
# In-memory only used to mean every Railway restart resent every reminder.
_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conversations",
    "reminded_events.json",
)


def _load_reminded() -> set[str]:
    if not os.path.exists(_STATE_FILE):
        return set()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_reminded(ids: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(ids), f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist reminded events: {e}")


_reminded_events: set[str] = _load_reminded()


def send_reminders():
    """Check for appointments in the next 24 hours and send reminders.

    Reminders always go via WhatsApp to the client's mobile number — even for
    Instagram-booked appointments, where the real mobile is collected in chat
    and stored on the event. Called by APScheduler every hour.
    """
    logger.info("Checking for upcoming appointments to send reminders...")

    appointments = google_calendar.get_upcoming_appointments(hours_ahead=24)

    for appt in appointments:
        event_id = appt["event_id"]

        # Skip if already reminded
        if event_id in _reminded_events:
            continue

        # Reminders ALWAYS go to the real mobile via WhatsApp, regardless of booking channel
        recipient = appt.get("mobile") or appt["phone"]
        language = appt.get("language", "en")
        summary = appt["summary"]
        date = appt["date"]
        time = appt["time"]

        if language == "ar":
            message = (
                f"مرحباً 👋\n\n"
                f"تذكير بموعدك في {config.BUSINESS_NAME}:\n\n"
                f"📋 الخدمة: {summary}\n"
                f"📅 التاريخ: {date}\n"
                f"🕐 الوقت: {time}\n\n"
                f"✅ للتأكيد: أرسل «أكد»\n"
                f"❌ للإلغاء: أرسل «إلغاء»\n"
                f"لأي تعديل آخر، راسلنا هنا.\n\n"
                f"شكراً، {config.BUSINESS_NAME}"
            )
        else:
            message = (
                f"Hi there 👋\n\n"
                f"This is a reminder of your appointment at {config.BUSINESS_NAME}:\n\n"
                f"📋 Service: {summary}\n"
                f"📅 Date: {date}\n"
                f"🕐 Time: {time}\n\n"
                f"✅ To confirm: reply 'confirm'\n"
                f"❌ To cancel: reply 'cancel'\n"
                f"For any other change, just reply here.\n\n"
                f"Thank you, {config.BUSINESS_NAME}"
            )

        try:
            whatsapp.send_message(recipient, message)
            _reminded_events.add(event_id)
            _save_reminded(_reminded_events)
            logger.info(f"Reminder sent via WhatsApp to {recipient} for event {event_id}")
        except Exception as e:
            logger.error(f"Failed to send WhatsApp reminder to {recipient}: {e}")
