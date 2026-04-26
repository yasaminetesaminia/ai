import logging

from services import google_calendar, whatsapp
import config

logger = logging.getLogger(__name__)

# Track which events already got a reminder (to avoid duplicates)
_reminded_events: set[str] = set()


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
            logger.info(f"Reminder sent via WhatsApp to {recipient} for event {event_id}")
        except Exception as e:
            logger.error(f"Failed to send WhatsApp reminder to {recipient}: {e}")
