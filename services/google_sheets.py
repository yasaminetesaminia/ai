"""Google Sheets booking storage — one worksheet per department.

Each clinic department gets its own tab inside the main spreadsheet:
Dentistry, Laser, Slimming, Beauty. Bookings get written into the tab
that matches their department, so the human receptionist can scan one
clean department view instead of filtering a single mixed sheet.

Rows in every tab are sorted by Appointment Date + Appointment Time
ascending after each write. That way "today 6 PM" always sits above
"tomorrow 3 PM" regardless of which booking was registered first —
fixes the real-world receptionist confusion of bookings appearing in
arrival order.

Public API stays compatible with the previous single-sheet version
(`add_client`, `delete_appointment`, `update_appointment_time`, etc.) so
sync.py and claude_ai.py don't need to change. The department-routing is
handled internally.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2 import service_account

import config
from services_config import DEPARTMENT_SHEET_NAMES

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_credentials = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
)
_gc = gspread.authorize(_credentials)
_spreadsheet = _gc.open_by_key(config.GOOGLE_SHEETS_ID)
# Kept for backward compat with services that still touch the legacy
# sheet1 (e.g. packages_sheet pulls _spreadsheet through this module).
_sheet = _spreadsheet.sheet1

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
_DATE_COL = HEADERS.index("Appointment Date")  # 5
_TIME_COL = HEADERS.index("Appointment Time")  # 6


# ---------------------------------------------------------------------------
# Worksheet handling
# ---------------------------------------------------------------------------
def _worksheet_for_dept(department: str):
    """Return the worksheet for a department, creating it (with headers) if missing."""
    name = DEPARTMENT_SHEET_NAMES.get(department, "Other")
    try:
        ws = _spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = _spreadsheet.add_worksheet(title=name, rows=200, cols=len(HEADERS))
        ws.append_row(HEADERS)
        return ws
    if not ws.row_values(1):
        ws.append_row(HEADERS)
    return ws


def _all_dept_worksheets():
    """Yield (dept_key, worksheet) for every department tab that exists."""
    for dept_key, name in DEPARTMENT_SHEET_NAMES.items():
        try:
            ws = _spreadsheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            continue
        yield dept_key, ws


def _sort_worksheet_by_appointment(ws) -> None:
    """Sort a department tab's rows in place by Appointment Date asc, then Time asc.

    Reads everything, sorts in Python, rewrites. Done after each insert so
    the receptionist always sees the next-soonest appointment at the top.
    """
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return
    header = rows[0]
    data = [r for r in rows[1:] if any(cell.strip() for cell in r)]
    if not data:
        return
    # Pad short rows so indexing is safe.
    width = len(header)
    data = [r + [""] * (width - len(r)) for r in data]
    data.sort(key=lambda r: (r[_DATE_COL], r[_TIME_COL]))
    # Rewrite in one batch.
    ws.clear()
    ws.append_row(header)
    ws.append_rows(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
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
    """Add a new client booking to the department's worksheet, then re-sort.

    `client_mobile` is the real phone number the client typed in chat. On
    Instagram `client_phone` is the IGSID (not human-readable), so we prefer
    `client_mobile` for the Phone column. On WhatsApp the two are equal.
    """
    ws = _worksheet_for_dept(department)
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
    ws.append_row(row)
    _sort_worksheet_by_appointment(ws)
    return True


def find_client(phone: str) -> dict | None:
    """Find a client by phone across all department tabs (most recent match)."""
    target = (phone or "").strip()
    for _dept_key, ws in _all_dept_worksheets():
        try:
            cell = ws.find(target)
        except gspread.exceptions.CellNotFound:
            continue
        if cell is None:
            continue
        row = ws.row_values(cell.row)
        return {
            "name": row[0] if len(row) > 0 else "",
            "phone": row[1] if len(row) > 1 else "",
            "department": row[2] if len(row) > 2 else "",
            "service": row[3] if len(row) > 3 else "",
            "doctor": row[4] if len(row) > 4 else "",
            "appointment_date": row[5] if len(row) > 5 else "",
            "appointment_time": row[6] if len(row) > 6 else "",
        }
    return None


def _find_row_in_ws(ws, phone: str, date: str, time: str) -> int | None:
    """Return 1-indexed row in `ws` matching phone + date + time, or None."""
    rows = ws.get_all_values()
    target_phone = (phone or "").strip()
    width = len(HEADERS)
    for idx, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (width - len(row))
        _name, p, _dept, _svc, _doc, appt_date, appt_time, *_ = padded[:width]
        if (
            p.strip() == target_phone
            and appt_date.strip() == date
            and appt_time.strip() == time
        ):
            return idx
    return None


def _find_in_all_depts(phone: str, date: str, time: str) -> tuple:
    """Search every dept tab for (phone, date, time). Returns (ws, row_idx, dept_key)."""
    for dept_key, ws in _all_dept_worksheets():
        idx = _find_row_in_ws(ws, phone, date, time)
        if idx is not None:
            return (ws, idx, dept_key)
    return (None, None, None)


def delete_appointment(phone: str, date: str, time: str) -> bool:
    """Delete the row matching phone + date + time across all dept tabs."""
    ws, idx, _dept = _find_in_all_depts(phone, date, time)
    if ws is None or idx is None:
        return False
    ws.delete_rows(idx)
    return True


def update_appointment_time(
    phone: str, old_date: str, old_time: str, new_date: str, new_time: str
) -> bool:
    """Move an existing row to a new date/time, then re-sort the dept tab."""
    ws, idx, _dept = _find_in_all_depts(phone, old_date, old_time)
    if ws is None or idx is None:
        return False
    # Appointment Date = column 6, Appointment Time = column 7 (1-indexed).
    ws.update_cell(idx, _DATE_COL + 1, new_date)
    ws.update_cell(idx, _TIME_COL + 1, new_time)
    _sort_worksheet_by_appointment(ws)
    return True


def get_appointments_in_range(start_date: str, end_date: str) -> list[dict]:
    """Aggregate bookings from every department tab whose Appointment Date
    falls within [start_date, end_date) (YYYY-MM-DD).

    Used by the weekly Telegram report and the bidirectional sync to see
    every booking — bot-made or manually entered.
    """
    results = []
    width = len(HEADERS)
    for dept_key, ws in _all_dept_worksheets():
        rows = ws.get_all_values()
        if len(rows) <= 1:
            continue
        for row in rows[1:]:
            padded = row + [""] * (width - len(row))
            name, phone, dept, service, doctor, appt_date, appt_time, new_client, _registered = padded[:width]
            if not appt_date or not appt_time:
                continue
            if not (start_date <= appt_date < end_date):
                continue
            results.append({
                "name": name.strip(),
                "phone": phone.strip(),
                # Prefer the explicit Department column, fall back to tab key.
                "department": (dept or dept_key).strip(),
                "service": service.strip(),
                "doctor": doctor.strip(),
                "date": appt_date.strip(),
                "time": appt_time.strip(),
                "is_new_client": new_client.strip().lower() == "yes",
            })
    return results
