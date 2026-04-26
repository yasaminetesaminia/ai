"""Retention campaigns: automated post-visit follow-ups.

Every day at 10:00 Oman the scheduler scans past appointments and sends any
due follow-up (day 1 aftercare, day 7 healing check, day 28 next-session, day
90/180 re-engagement, day 365 annual checkup). Campaigns are per-service so
a dental implant gets the healing track while a laser session gets the
next-session nudge.

Delivery is always via WhatsApp (to the client's mobile from the event's
`Mobile:` field) regardless of which channel the appointment was booked on —
most replies come naturally on WhatsApp anyway. If the client replies, it
lands on the normal webhook and Claude handles it.

State of "already sent" is kept in conversations/retention_sent.json so
restarts and duplicate scans never double-send.
"""

import json
import logging
import os
import threading
from datetime import date as date_cls, datetime
from typing import Iterable

import config
from services import google_calendar, whatsapp
from services_config import SERVICES

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conversations",
    "retention_sent.json",
)
_lock = threading.Lock()

# Campaign schedule per (department, sub_service). "_any_" matches all
# sub_services of the department. Each tuple is (campaign_name, offset_days).
CAMPAIGNS_BY_SERVICE: dict[tuple[str, str], list[tuple[str, int]]] = {
    ("dentistry", "implant"): [
        ("aftercare", 1),
        ("healing_check", 7),
        ("annual", 365),
    ],
    ("dentistry", "root_canal"): [
        ("aftercare", 1),
        ("healing_check", 7),
    ],
    ("dentistry", "veneer"): [
        ("aftercare", 1),
        ("annual", 365),
    ],
    ("dentistry", "filling"): [
        ("aftercare", 1),
    ],
    ("dentistry", "checkup"): [
        ("annual", 365),
    ],
    ("laser_hair_removal", "_any_"): [
        ("next_session", 28),
        ("reengage", 180),
    ],
    ("slimming", "_any_"): [
        ("feedback", 7),
        ("next_session", 14),
        ("reengage", 90),
    ],
    ("beauty", "botox"): [
        ("aftercare", 1),
        ("touchup_window", 90),
    ],
    ("beauty", "filler"): [
        ("aftercare", 1),
        ("retreat_window", 180),
    ],
    ("beauty", "skin_lift"): [
        ("aftercare", 1),
        ("reengage", 180),
    ],
    ("beauty", "laser_spots_wrinkles"): [
        ("aftercare", 1),
        ("reengage", 90),
    ],
}

# One message per (campaign, language). `{name}`, `{service}`, `{business}`
# are interpolated at send time.
TEMPLATES: dict[tuple[str, str], str] = {
    ("aftercare", "en"): (
        "Hi {name} 👋\n\n"
        "Thank you for visiting {business}! How are you feeling after your "
        "{service} session? If you have any concerns or questions, just reply "
        "here — we're happy to help."
    ),
    ("aftercare", "ar"): (
        "مرحباً {name} 👋\n\n"
        "شكراً لزيارتك {business}! كيف تشعر بعد جلسة {service}؟ إذا كان لديك "
        "أي استفسار أو قلق، فقط أرسل هنا — نحن سعداء بمساعدتك."
    ),
    ("healing_check", "en"): (
        "Hi {name} 👋\n\n"
        "It's been a week since your {service} at {business}. We want to make "
        "sure everything is healing well. Any pain, swelling, or concerns? "
        "Reply here anytime."
    ),
    ("healing_check", "ar"): (
        "مرحباً {name} 👋\n\n"
        "مر أسبوع على {service} في {business}. نريد الاطمئنان على تعافيك. "
        "هل هناك ألم أو تورم أو أي ملاحظة؟ أرسل هنا في أي وقت."
    ),
    ("next_session", "en"): (
        "Hi {name}! ✨\n\n"
        "It's been about a month since your {service} session at {business}. "
        "For best results, many clients book their next session around this "
        "time. Ready to schedule? Just reply."
    ),
    ("next_session", "ar"): (
        "مرحباً {name}! ✨\n\n"
        "مر حوالي شهر على جلسة {service} في {business}. للحصول على أفضل "
        "النتائج، يحجز معظم العملاء الجلسة التالية في هذا الوقت. جاهز للحجز؟ "
        "فقط أرسل."
    ),
    ("feedback", "en"): (
        "Hi {name}! 🌸\n\n"
        "It's been a week since your {service} session at {business}. How do "
        "you feel? We'd love to hear your thoughts and help you plan the next "
        "step."
    ),
    ("feedback", "ar"): (
        "مرحباً {name}! 🌸\n\n"
        "مر أسبوع على جلسة {service} في {business}. كيف تشعر؟ يسعدنا سماع "
        "انطباعك ومساعدتك في التخطيط للخطوة التالية."
    ),
    ("touchup_window", "en"): (
        "Hi {name}! 💫\n\n"
        "It's been about 3 months since your Botox at {business} — that's the "
        "typical touch-up window. Would you like to book a refresh session? "
        "Just reply."
    ),
    ("touchup_window", "ar"): (
        "مرحباً {name}! 💫\n\n"
        "مر حوالي 3 أشهر على جلسة البوتوكس في {business} — هذا هو الوقت "
        "المعتاد للتجديد. هل تودين حجز جلسة تجديد؟ فقط أرسلي."
    ),
    ("retreat_window", "en"): (
        "Hi {name}! 💖\n\n"
        "Most filler treatments last about 6 months. Yours is at the refresh "
        "window if you'd like to maintain the look. Want to book a session?"
    ),
    ("retreat_window", "ar"): (
        "مرحباً {name}! 💖\n\n"
        "تستمر معظم جلسات الفيلر حوالي 6 أشهر، وقد حان وقت التجديد إذا كنت "
        "ترغبين في الحفاظ على المظهر. هل تودين حجز جلسة؟"
    ),
    ("annual", "en"): (
        "Hi {name}! 🦷\n\n"
        "It's been a year since your last dental visit at {business}. Time "
        "for your annual checkup? Reply and we'll find you a slot."
    ),
    ("annual", "ar"): (
        "مرحباً {name}! 🦷\n\n"
        "مر عام على آخر زيارة أسنان لك في {business}. حان وقت الفحص السنوي؟ "
        "أرسل وسنجد لك موعداً مناسباً."
    ),
    ("reengage", "en"): (
        "Hi {name}! 🌿\n\n"
        "We miss seeing you at {business}. If you'd like to book your next "
        "{service} session, just reply and we'll find you a great time."
    ),
    ("reengage", "ar"): (
        "مرحباً {name}! 🌿\n\n"
        "نفتقد رؤيتك في {business}. إذا كنت ترغب في حجز جلسة {service} "
        "التالية، فقط أرسل وسنجد لك وقتاً مناسباً."
    ),
}


def _load_sent() -> set[str]:
    if not os.path.exists(_STATE_FILE):
        return set()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_sent(keys: Iterable[str]) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f, ensure_ascii=False, indent=2)


def _campaigns_for(department: str, sub_service: str) -> list[tuple[str, int]]:
    """Lookup per-service campaigns, falling back to _any_ at the dept level."""
    exact = CAMPAIGNS_BY_SERVICE.get((department, sub_service))
    if exact is not None:
        return exact
    return CAMPAIGNS_BY_SERVICE.get((department, "_any_"), [])


def _service_display_name(department: str, sub_service: str) -> str:
    """Human-readable service name for the message body."""
    dept = SERVICES.get(department)
    if not dept:
        return sub_service.replace("_", " ")
    svc = dept["sub_services"].get(sub_service)
    if not svc:
        return sub_service.replace("_", " ")
    return svc["name"]


def _build_message(
    campaign: str, language: str, client_name: str, service_name: str
) -> str | None:
    template = TEMPLATES.get((campaign, language))
    if template is None:
        template = TEMPLATES.get((campaign, "en"))
    if template is None:
        return None
    return template.format(
        name=client_name or ("صديقنا" if language == "ar" else "there"),
        service=service_name,
        business=config.BUSINESS_NAME,
    )


def _dedup_key(phone: str, appt_date: str, appt_time: str, campaign: str) -> str:
    return f"{phone}|{appt_date}|{appt_time}|{campaign}"


def run_retention(today: date_cls | None = None) -> int:
    """Scan the last 400 days of appointments and send any follow-ups that
    are due today. Returns the number of messages sent.
    """
    today = today or datetime.now().date()
    sent_keys = _load_sent()
    new_sends: list[str] = []

    appointments = google_calendar.get_past_appointments(days_back=400)
    logger.info(
        "Retention scan: %d past appointments to evaluate for %s",
        len(appointments),
        today.isoformat(),
    )

    for appt in appointments:
        department = appt.get("department", "")
        sub_service = appt.get("sub_service", "")
        if not department or not sub_service:
            continue

        try:
            appt_date = datetime.strptime(appt["date"], "%Y-%m-%d").date()
        except ValueError:
            continue

        campaigns = _campaigns_for(department, sub_service)
        days_since = (today - appt_date).days

        for campaign, offset_days in campaigns:
            if days_since != offset_days:
                continue

            key = _dedup_key(appt["phone"], appt["date"], appt["time"], campaign)
            if key in sent_keys:
                continue

            recipient = appt.get("mobile") or appt["phone"]
            body = _build_message(
                campaign=campaign,
                language=appt.get("language", "en"),
                client_name=appt.get("client_name", ""),
                service_name=_service_display_name(department, sub_service),
            )
            if body is None:
                logger.warning("No template for campaign=%s", campaign)
                continue

            try:
                whatsapp.send_message(recipient, body)
                new_sends.append(key)
                logger.info(
                    "Retention sent: %s to %s (service=%s/%s, appt=%s)",
                    campaign, recipient, department, sub_service, appt["date"],
                )
            except Exception as e:
                logger.error("Retention send failed for %s: %s", recipient, e)

    if new_sends:
        with _lock:
            merged = _load_sent() | set(new_sends)
            _save_sent(merged)

    return len(new_sends)
