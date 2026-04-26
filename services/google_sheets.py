from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2 import service_account

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_credentials = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
)
_gc = gspread.authorize(_credentials)
_sheet = _gc.open_by_key(config.GOOGLE_SHEETS_ID).sheet1

HEADERS = [
    "Name",
    "Phone",
    "Department",
    "Service",
    "Doctor",
    "Appointment Date",
    "Appointment Time",
    "New Client",
    "Registered At",
]


def _ensure_headers():
    """Add headers if the sheet is empty."""
    if not _sheet.row_values(1):
        _sheet.append_row(HEADERS)


def add_client(
    client_name: str,
    client_phone: str,
    department: str,
    sub_service: str,
    doctor: str,
    appointment_date: str,
    appointment_time: str,
    is_new_client: bool,
    client_mobile: str | None = None,
) -> bool:
    """Add a new client record to the Google Sheet.

    `client_mobile` is the real phone number the client typed in chat. On
    Instagram `client_phone` is the IGSID (not human-readable), so we prefer
    `client_mobile` for the Phone column. On WhatsApp the two are equal.
    """
    _ensure_headers()
    phone_for_sheet = client_mobile or client_phone
    now = datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    row = [
        client_name,
        phone_for_sheet,
        department,
        sub_service,
        doctor or "",
        appointment_date,
        appointment_time,
        "Yes" if is_new_client else "No",
        now,
    ]
    _sheet.append_row(row)
    return True


def find_client(phone: str) -> dict | None:
    """Find a client by phone number."""
    _ensure_headers()
    try:
        cell = _sheet.find(phone)
    except gspread.exceptions.CellNotFound:
        return None

    if cell is None:
        return None

    row = _sheet.row_values(cell.row)
    return {
        "name": row[0] if len(row) > 0 else "",
        "phone": row[1] if len(row) > 1 else "",
        "department": row[2] if len(row) > 2 else "",
        "service": row[3] if len(row) > 3 else "",
        "doctor": row[4] if len(row) > 4 else "",
        "appointment_date": row[5] if len(row) > 5 else "",
        "appointment_time": row[6] if len(row) > 6 else "",
    }


def _find_row_index(phone: str, date: str, time: str) -> int | None:
    """Locate the 1-indexed Sheet row matching this phone + date + time, or None."""
    _ensure_headers()
    rows = _sheet.get_all_values()
    target_phone = (phone or "").strip()
    for idx, row in enumerate(rows[1:], start=2):  # 1-based row number; skip header
        padded = row + [""] * (9 - len(row))
        _name, p, _dept, _svc, _doc, appt_date, appt_time, *_ = padded[:9]
        if (
            p.strip() == target_phone
            and appt_date.strip() == date
            and appt_time.strip() == time
        ):
            return idx
    return None


def delete_appointment(phone: str, date: str, time: str) -> bool:
    """Delete the row matching this phone + date + time. Used on cancel so
    the bidirectional sync doesn't resurrect a deleted Calendar event.
    """
    idx = _find_row_index(phone, date, time)
    if idx is None:
        return False
    _sheet.delete_rows(idx)
    return True


def update_appointment_time(
    phone: str, old_date: str, old_time: str, new_date: str, new_time: str
) -> bool:
    """Move an existing row to a new date/time so reschedule keeps Sheet
    consistent with Calendar.
    """
    idx = _find_row_index(phone, old_date, old_time)
    if idx is None:
        return False
    # Appointment Date = column 6, Appointment Time = column 7 in HEADERS
    _sheet.update_cell(idx, 6, new_date)
    _sheet.update_cell(idx, 7, new_time)
    return True


def get_appointments_in_range(start_date: str, end_date: str) -> list[dict]:
    """Return every row whose Appointment Date falls within [start_date, end_date) (YYYY-MM-DD).

    Used by the weekly Telegram report to include BOTH bot-booked appointments
    (synced from Calendar) AND manual entries the human receptionist added
    directly in the Sheet.
    """
    _ensure_headers()
    rows = _sheet.get_all_values()
    if len(rows) <= 1:
        return []

    results = []
    for row in rows[1:]:  # skip header
        padded = row + [""] * (9 - len(row))
        name, phone, department, service, doctor, appt_date, appt_time, new_client, _registered = padded[:9]

        if not appt_date or not appt_time:
            continue
        if not (start_date <= appt_date < end_date):
            continue

        results.append({
            "name": name.strip(),
            "phone": phone.strip(),
            "department": department.strip(),
            "service": service.strip(),
            "doctor": doctor.strip(),
            "date": appt_date.strip(),
            "time": appt_time.strip(),
            "is_new_client": new_client.strip().lower() == "yes",
        })
    return results
