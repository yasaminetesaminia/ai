"""Bidirectional Google Sheet ↔ Google Calendar sync.

Runs periodically. Any manual entry a human receptionist adds OR removes
on one side is mirrored to the other so the bot sees a unified view of
availability and the clinic sees every appointment in both places.

Dedup key = (department_key, date, time, normalized_client_name).

Deletion semantics need state because "missing on one side" alone is
ambiguous (was it just added on the other side, or was it deleted on this
side?). We persist the set of dedup keys we've already mirrored to
`conversations/sync_state.json`. If a key was previously seen on both
sides and one side is now missing it, that's a manual delete → propagate
the deletion. If a key is brand new (not in seen state) and only on one
side, that's a manual add → propagate the addition.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from services import google_sheets, google_calendar
from services_config import SERVICES, get_service_duration

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_credentials = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIALS_FILE, scopes=_SCOPES
)
_service = build("calendar", "v3", credentials=_credentials)
_tz = ZoneInfo(config.BUSINESS_TIMEZONE)

SYNC_WINDOW_DAYS = 60

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conversations",
    "sync_state.json",
)

DEPARTMENT_LABELS = {
    "dermatology": "Dermatology & Skin Care",
    "aesthetics": "Non-Surgical Aesthetics",
    "regenerative": "Regenerative Therapies",
    "slimming": "Body Slimming",
    "gynecology": "Aesthetic Gynecology",
    "laser_hair_removal": "Laser Hair Removal",
}


def _normalize_department(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if s in DEPARTMENT_LABELS:
        return s
    # Loose matching for human-typed event titles. Order matters: more
    # specific keywords first so 'laser hair' beats the generic 'laser'
    # match used by dermatology lasers (Frax, Picoway, etc).
    if "laser hair" in s or s == "laser":
        return "laser_hair_removal"
    if "regen" in s or "prp" in s or "exosome" in s or "stem cell" in s:
        return "regenerative"
    if "gyne" in s or "vagino" in s or "labia" in s:
        return "gynecology"
    if "derma" in s or "skin" in s:
        return "dermatology"
    if "aesthet" in s or "botox" in s or "filler" in s or "thread" in s:
        return "aesthetics"
    if "slim" in s or "body" in s:
        return "slimming"
    return None


def _find_sub_service(dept_key: str, service_raw: str) -> tuple[str, int]:
    """Map a human-typed service name (or key) to (sub_service, duration_minutes)."""
    if not service_raw:
        return service_raw, 30
    dept = SERVICES.get(dept_key)
    if not dept:
        return service_raw, 30

    target = service_raw.strip().lower()
    sub_services = dept["sub_services"]

    if target in sub_services:
        return target, get_service_duration(dept_key, target)

    for key, svc in sub_services.items():
        if svc["name"].strip().lower() == target:
            return key, get_service_duration(dept_key, key)

    for key, svc in sub_services.items():
        name_lc = svc["name"].lower()
        if target in name_lc or name_lc in target:
            return key, get_service_duration(dept_key, key)

    return service_raw, 30


_SUMMARY_RE = re.compile(r"^\[([^\]]+)\]\s*(.+?)(?:\s*\(([^)]+)\))?$")


def _parse_description(description: str) -> dict:
    out = {}
    for line in description.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip().lower()] = value.strip()
    return out


def _fetch_calendar_events(start: datetime, end: datetime) -> list[dict]:
    result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=1000,
    ).execute()
    return result.get("items", [])


def _extract_calendar_appts(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        summary = ev.get("summary", "")
        m = _SUMMARY_RE.match(summary)
        if not m:
            continue
        dept = _normalize_department(m.group(1))
        if not dept:
            continue
        start_iso = ev.get("start", {}).get("dateTime")
        end_iso = ev.get("end", {}).get("dateTime")
        if not start_iso or not end_iso:
            continue
        start_dt = datetime.fromisoformat(start_iso).astimezone(_tz)
        end_dt = datetime.fromisoformat(end_iso).astimezone(_tz)
        desc = _parse_description(ev.get("description", ""))
        client_name = desc.get("client") or ""
        if not client_name:
            rest = m.group(2).strip()
            if " - " in rest:
                _, _, client_name = rest.rpartition(" - ")
                client_name = client_name.strip()
        service = desc.get("service") or m.group(2).strip()
        doctor = desc.get("doctor") or (m.group(3).strip() if m.group(3) else "")
        duration_min = int((end_dt - start_dt).total_seconds() // 60)
        out.append({
            "event_id": ev["id"],
            "department": dept,
            "date": start_dt.strftime("%Y-%m-%d"),
            "time": start_dt.strftime("%H:%M"),
            "service": service,
            "client_name": client_name,
            "phone": desc.get("phone", ""),
            "mobile": desc.get("mobile") or desc.get("phone", ""),
            "doctor": doctor,
            "channel": desc.get("channel", "manual"),
            "language": desc.get("language", "en"),
            "duration": duration_min,
        })
    return out


def _dedup_key(department: str, date_str: str, time_str: str, name: str) -> tuple:
    return (department, date_str, time_str, (name or "").strip().lower())


def _key_to_str(key_tuple: tuple) -> str:
    return "|".join(str(x) for x in key_tuple)


def _load_seen_keys() -> set[str]:
    if not os.path.exists(_STATE_FILE):
        return set()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        logger.warning(f"Sync state load failed, starting fresh: {e}")
        return set()


def _save_seen_keys(keys: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(keys), f, indent=2)
    except Exception as e:
        logger.error(f"Sync state save failed: {e}")


def _build_indexes(start: datetime, end: datetime) -> tuple[dict, dict]:
    cal_events = _fetch_calendar_events(start, end)
    cal_appts = _extract_calendar_appts(cal_events)
    cal_index: dict[tuple, dict] = {}
    for a in cal_appts:
        if not a["client_name"]:
            continue
        cal_index[_dedup_key(a["department"], a["date"], a["time"], a["client_name"])] = a

    sheet_index: dict[tuple, dict] = {}
    sheet_rows = google_sheets.get_appointments_in_range(
        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    for row in sheet_rows:
        dept_key = _normalize_department(row["department"])
        if not dept_key or not row["name"]:
            continue
        sheet_index[_dedup_key(dept_key, row["date"], row["time"], row["name"])] = {
            **row, "department_key": dept_key,
        }

    return sheet_index, cal_index


def _add_to_calendar(row: dict) -> bool:
    sub_service_key, duration = _find_sub_service(row["department_key"], row["service"])
    phone = (row["phone"] or "").strip() or "unknown"
    try:
        google_calendar.book_appointment(
            client_name=row["name"],
            client_phone=phone,
            department=row["department_key"],
            sub_service=sub_service_key,
            date_str=row["date"],
            time_str=row["time"],
            duration_minutes=duration,
            doctor=row["doctor"] or None,
            channel="manual",
            language="en",
            client_mobile=phone,
        )
        return True
    except Exception as e:
        logger.error(f"Sync add Calendar failed for {row.get('name')}: {e}")
        return False


def _add_to_sheet(appt: dict) -> bool:
    phone = appt["mobile"] or appt["phone"] or "unknown"
    try:
        google_sheets.add_client(
            client_name=appt["client_name"],
            client_phone=phone,
            department=appt["department"],
            sub_service=appt["service"],
            doctor=appt["doctor"] or "",
            appointment_date=appt["date"],
            appointment_time=appt["time"],
            is_new_client=False,
            client_mobile=phone,
        )
        return True
    except Exception as e:
        logger.error(f"Sync add Sheet failed for {appt.get('client_name')}: {e}")
        return False


def sync_all():
    """Bidirectional add+delete sync. Scheduled periodically."""
    try:
        now = datetime.now(_tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=SYNC_WINDOW_DAYS)

        sheet_idx, cal_idx = _build_indexes(start, end)
        seen = _load_seen_keys()

        added_cal = added_sheet = deleted_cal = deleted_sheet = 0
        new_seen: set[str] = set()

        all_keys = set(sheet_idx.keys()) | set(cal_idx.keys())
        for k in all_keys:
            k_str = _key_to_str(k)
            in_sheet = k in sheet_idx
            in_cal = k in cal_idx
            in_seen = k_str in seen

            if in_sheet and in_cal:
                # Steady state — nothing to do, just remember it.
                new_seen.add(k_str)

            elif in_sheet and not in_cal:
                if in_seen:
                    # Was on both sides last cycle → Calendar event was deleted manually.
                    row = sheet_idx[k]
                    if google_sheets.delete_appointment(row["phone"], row["date"], row["time"]):
                        deleted_sheet += 1
                        logger.info(f"Sync deleted Sheet row (Calendar removed): {k}")
                    # Drop from seen state (don't add to new_seen).
                else:
                    # Brand new Sheet entry → propagate to Calendar.
                    if _add_to_calendar(sheet_idx[k]):
                        added_cal += 1
                        new_seen.add(k_str)
                        logger.info(f"Sync added Calendar event (new Sheet entry): {k}")

            elif in_cal and not in_sheet:
                if in_seen:
                    # Was on both sides last cycle → Sheet row was deleted manually.
                    if google_calendar.delete_event(cal_idx[k]["event_id"]):
                        deleted_cal += 1
                        logger.info(f"Sync deleted Calendar event (Sheet removed): {k}")
                else:
                    # Brand new Calendar event → propagate to Sheet.
                    if _add_to_sheet(cal_idx[k]):
                        added_sheet += 1
                        new_seen.add(k_str)
                        logger.info(f"Sync added Sheet row (new Calendar entry): {k}")

        _save_seen_keys(new_seen)

        if added_cal or added_sheet or deleted_cal or deleted_sheet:
            logger.info(
                f"Sync cycle: +{added_cal} cal, +{added_sheet} sheet, "
                f"-{deleted_cal} cal, -{deleted_sheet} sheet"
            )
    except Exception as e:
        logger.error(f"sync_all failed: {e}", exc_info=True)
