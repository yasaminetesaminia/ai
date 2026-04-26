import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from services_config import get_capacity, get_service_duration

# Prevents two concurrent bookings for the same slot. `check_available_slots`
# and creating the event are not atomic in Google Calendar, so two parallel
# webhook requests could both pass the availability check and then both
# insert an event. This lock serializes the verify-then-insert step across
# the Flask threaded workers.
_booking_lock = threading.Lock()


class SlotNoLongerAvailable(Exception):
    """Raised when the requested slot got filled between check and book."""

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_credentials = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
)
_service = build("calendar", "v3", credentials=_credentials)
_tz = ZoneInfo(config.BUSINESS_TIMEZONE)


def _is_closed_day(date: datetime) -> bool:
    """Check if a given date is on the closed day (Friday)."""
    closed_day = config.BUSINESS_CLOSED_DAY.lower()
    day_name = date.strftime("%A").lower()
    return day_name == closed_day


def _is_holiday(date: datetime) -> bool:
    """Check if a given date is a public holiday."""
    return date.strftime("%Y-%m-%d") in config.BUSINESS_HOLIDAYS


def _is_break_time(slot_start: datetime, slot_end: datetime) -> bool:
    """Check if a slot overlaps with the staff break time (14:00-15:00)."""
    break_start_h, break_start_m = map(int, config.BUSINESS_BREAK_START.split(":"))
    break_end_h, break_end_m = map(int, config.BUSINESS_BREAK_END.split(":"))

    break_start = slot_start.replace(hour=break_start_h, minute=break_start_m, second=0)
    break_end = slot_start.replace(hour=break_end_h, minute=break_end_m, second=0)

    # Check overlap with break
    return slot_start < break_end and slot_end > break_start


def _get_end_time(department: str) -> str:
    """Get the end time for a department. Laser runs until 23:00, others until 20:00."""
    if department == "laser_hair_removal":
        return config.BUSINESS_LASER_END
    return config.BUSINESS_WORKING_HOURS_END


def _get_events_for_day(date_str: str, department: str) -> list[dict]:
    """Fetch all events for a given date, covering the full possible range."""
    date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_tz)
    start_hour, start_min = map(int, config.BUSINESS_WORKING_HOURS_START.split(":"))

    end_time = _get_end_time(department)
    end_hour, end_min = map(int, end_time.split(":"))

    day_start = date.replace(hour=start_hour, minute=start_min, second=0)
    day_end = date.replace(hour=end_hour, minute=end_min, second=0)

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events_result.get("items", [])


def _count_overlapping(events: list[dict], slot_start: datetime, slot_end: datetime, department: str, doctor: str | None = None) -> int:
    """Count how many existing events overlap with a time slot for a specific department."""
    count = 0
    for event in events:
        summary = event.get("summary", "")

        # Check if event belongs to this department
        if f"[{department}]" not in summary:
            continue

        # For beauty department, filter by doctor
        if doctor and doctor not in summary:
            continue

        ev_start = datetime.fromisoformat(event["start"]["dateTime"])
        ev_end = datetime.fromisoformat(event["end"]["dateTime"])

        # Check overlap
        if slot_start < ev_end and slot_end > ev_start:
            count += 1

    return count


def get_available_slots(
    date_str: str,
    department: str,
    sub_service: str,
    units: int = 1,
    doctor: str | None = None,
) -> list[str]:
    """Return available time slots for a given date, department, and service.

    Takes into account:
    - Department capacity (concurrent slots)
    - Service duration
    - Break time (14:00-15:00)
    - Closed day (Friday)
    - Laser department extended hours (until 23:00)
    - Appointment must finish before closing time
    """
    date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_tz)

    # Check if closed day or holiday
    if _is_closed_day(date) or _is_holiday(date):
        return []

    start_hour, start_min = map(int, config.BUSINESS_WORKING_HOURS_START.split(":"))
    end_time = _get_end_time(department)
    end_hour, end_min = map(int, end_time.split(":"))

    day_start = date.replace(hour=start_hour, minute=start_min, second=0)
    day_end = date.replace(hour=end_hour, minute=end_min, second=0)

    events = _get_events_for_day(date_str, department)
    duration = timedelta(minutes=get_service_duration(department, sub_service, units))
    capacity = get_capacity(department)

    # For today, skip slots that have already passed (give 15-min buffer)
    now = datetime.now(_tz)
    earliest_today = now + timedelta(minutes=15) if date.date() == now.date() else None

    available = []
    current = day_start
    step = timedelta(minutes=5)

    while current + duration <= day_end:
        slot_end = current + duration

        # Skip past slots for today
        if earliest_today and current < earliest_today:
            current += step
            continue

        # Skip break time (14:00-15:00)
        if _is_break_time(current, slot_end):
            current += step
            continue

        overlap_count = _count_overlapping(events, current, slot_end, department, doctor)

        if overlap_count < capacity:
            available.append(current.strftime("%H:%M"))
            current += timedelta(minutes=15)
        else:
            current += step

    return available


def book_appointment(
    client_name: str,
    client_phone: str,
    department: str,
    sub_service: str,
    date_str: str,
    time_str: str,
    duration_minutes: int,
    doctor: str | None = None,
    channel: str = "whatsapp",
    language: str = "en",
    client_mobile: str | None = None,
    package_id: str | None = None,
) -> dict:
    """Book an appointment and return the created event.

    `channel` is recorded in the event description so the reminder job knows
    which channel the client booked on. `language` ("en" or "ar") is recorded
    so the reminder can be sent in the client's chosen language. `client_mobile`
    is the real phone number and is ALWAYS used as the WhatsApp destination
    for the 24h reminder — on Instagram bookings it's the number the client
    typed in chat; on WhatsApp bookings it equals client_phone.
    """
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=_tz)
    end_dt = dt + timedelta(minutes=duration_minutes)

    summary = f"[{department}] {sub_service} - {client_name}"
    if doctor:
        summary = f"[{department}] {sub_service} - {client_name} ({doctor})"

    mobile_for_reminder = client_mobile or client_phone

    description = (
        f"Client: {client_name}\n"
        f"Phone: {client_phone}\n"
        f"Mobile: {mobile_for_reminder}\n"
        f"Department: {department}\n"
        f"Service: {sub_service}\n"
        f"Duration: {duration_minutes} min\n"
        f"Channel: {channel}\n"
        f"Language: {language}"
    )
    if doctor:
        description += f"\nDoctor: {doctor}"
    if package_id:
        description += f"\nPackage: {package_id}"

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": dt.isoformat(), "timeZone": config.BUSINESS_TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": config.BUSINESS_TIMEZONE},
    }

    with _booking_lock:
        # Re-verify capacity under the lock so parallel bookings for the same
        # slot can't both pass. We re-read the live calendar here rather than
        # trusting the availability check from earlier in the conversation.
        capacity = get_capacity(department)
        live_events = _get_events_for_day(date_str, department)
        overlap = _count_overlapping(live_events, dt, end_dt, department, doctor)
        if overlap >= capacity:
            raise SlotNoLongerAvailable(
                f"Slot {date_str} {time_str} for {department} is already full."
            )

        created = _service.events().insert(
            calendarId=config.GOOGLE_CALENDAR_ID, body=event
        ).execute()

    return {"event_id": created["id"], "date": date_str, "time": time_str}


def cancel_appointment(client_phone: str) -> dict | None:
    """Cancel the nearest upcoming appointment. Returns details of the cancelled
    event (or None if not found) so the caller can also delete the Sheet row.
    """
    now = datetime.now(_tz).isoformat()

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=now,
        q=client_phone,
        singleEvents=True,
        orderBy="startTime",
        maxResults=1,
    ).execute()

    events = events_result.get("items", [])
    if not events:
        return None

    event = events[0]
    start = datetime.fromisoformat(event["start"]["dateTime"])
    description = event.get("description", "")
    mobile = None
    phone_in_desc = None
    client_name = ""
    package_id = None
    for line in description.split("\n"):
        if line.startswith("Mobile:"):
            mobile = line.replace("Mobile:", "").strip()
        elif line.startswith("Phone:"):
            phone_in_desc = line.replace("Phone:", "").strip()
        elif line.startswith("Client:"):
            client_name = line.replace("Client:", "").strip()
        elif line.startswith("Package:"):
            package_id = line.replace("Package:", "").strip()

    _service.events().delete(
        calendarId=config.GOOGLE_CALENDAR_ID, eventId=event["id"]
    ).execute()

    return {
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "mobile": mobile or phone_in_desc or client_phone,
        "client_name": client_name,
        "summary": event.get("summary", ""),
        "package_id": package_id,
    }


def delete_event(event_id: str) -> bool:
    """Delete a Calendar event by ID. Returns False if already gone."""
    try:
        _service.events().delete(
            calendarId=config.GOOGLE_CALENDAR_ID, eventId=event_id
        ).execute()
        return True
    except Exception:
        return False


def reschedule_appointment(
    client_phone: str, new_date: str, new_time: str
) -> dict | None:
    """Reschedule the nearest upcoming appointment for a client."""
    now = datetime.now(_tz).isoformat()

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=now,
        q=client_phone,
        singleEvents=True,
        orderBy="startTime",
        maxResults=1,
    ).execute()

    events = events_result.get("items", [])
    if not events:
        return None

    event = events[0]

    # Calculate original duration
    orig_start = datetime.fromisoformat(event["start"]["dateTime"])
    orig_end = datetime.fromisoformat(event["end"]["dateTime"])
    duration = orig_end - orig_start

    old_date = orig_start.strftime("%Y-%m-%d")
    old_time = orig_start.strftime("%H:%M")

    description = event.get("description", "")
    mobile = None
    phone_in_desc = None
    for line in description.split("\n"):
        if line.startswith("Mobile:"):
            mobile = line.replace("Mobile:", "").strip()
        elif line.startswith("Phone:"):
            phone_in_desc = line.replace("Phone:", "").strip()

    new_dt = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M").replace(tzinfo=_tz)
    new_end = new_dt + duration

    event["start"] = {"dateTime": new_dt.isoformat(), "timeZone": config.BUSINESS_TIMEZONE}
    event["end"] = {"dateTime": new_end.isoformat(), "timeZone": config.BUSINESS_TIMEZONE}

    updated = _service.events().update(
        calendarId=config.GOOGLE_CALENDAR_ID,
        eventId=event["id"],
        body=event,
    ).execute()

    return {
        "event_id": updated["id"],
        "old_date": old_date,
        "old_time": old_time,
        "new_date": new_date,
        "new_time": new_time,
        "mobile": mobile or phone_in_desc or client_phone,
    }


def get_client_appointment(client_phone: str) -> dict | None:
    """Find the nearest upcoming appointment for a client (by phone or WhatsApp ID)."""
    now = datetime.now(_tz).isoformat()

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=now,
        q=client_phone,
        singleEvents=True,
        orderBy="startTime",
        maxResults=1,
    ).execute()

    events = events_result.get("items", [])
    if not events:
        return None

    event = events[0]
    start = datetime.fromisoformat(event["start"]["dateTime"])
    return {
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
        "summary": event.get("summary", ""),
        "description": event.get("description", ""),
    }


def _parse_event(event: dict) -> dict | None:
    """Parse a calendar event into the clinic's appointment dict. Returns None
    if the event lacks a phone (i.e. it's not a bot/sheet-managed appointment).
    """
    description = event.get("description", "")
    phone = None
    mobile = None
    channel = "whatsapp"  # legacy events default to WhatsApp
    language = "en"  # legacy events default to English
    client_name = ""
    department = ""
    sub_service = ""
    for line in description.split("\n"):
        if line.startswith("Phone:"):
            phone = line.replace("Phone:", "").strip()
        elif line.startswith("Mobile:"):
            mobile = line.replace("Mobile:", "").strip()
        elif line.startswith("Channel:"):
            channel = line.replace("Channel:", "").strip() or "whatsapp"
        elif line.startswith("Language:"):
            language = line.replace("Language:", "").strip() or "en"
        elif line.startswith("Client:"):
            client_name = line.replace("Client:", "").strip()
        elif line.startswith("Department:"):
            department = line.replace("Department:", "").strip()
        elif line.startswith("Service:"):
            sub_service = line.replace("Service:", "").strip()

    if not phone:
        return None

    start = datetime.fromisoformat(event["start"]["dateTime"])
    return {
        "event_id": event["id"],
        "summary": event.get("summary", ""),
        "client_name": client_name,
        "phone": phone,
        "mobile": mobile or phone,
        "channel": channel,
        "language": language,
        "department": department,
        "sub_service": sub_service,
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H:%M"),
    }


def get_upcoming_appointments(hours_ahead: int = 24) -> list[dict]:
    """Get appointments happening in the next N hours (for reminders)."""
    now = datetime.now(_tz)
    future = now + timedelta(hours=hours_ahead)

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=future.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    appointments = []
    for event in events_result.get("items", []):
        parsed = _parse_event(event)
        if parsed:
            appointments.append(parsed)
    return appointments


def get_past_appointments(days_back: int) -> list[dict]:
    """Return appointments whose start was within the last `days_back` days.

    Used by retention campaigns (day-1/7/28/90/180/365 follow-ups) to find
    which clients are due for a post-visit message today.
    """
    now = datetime.now(_tz)
    past = now - timedelta(days=days_back)

    events_result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=past.isoformat(),
        timeMax=now.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    appointments = []
    for event in events_result.get("items", []):
        parsed = _parse_event(event)
        if parsed:
            appointments.append(parsed)
    return appointments
