"""Google-Sheets-driven registration for pre-paid session packages.

Workflow (receptionist's perspective):
  1. Client pays at reception for a package.
  2. Receptionist opens the same Google Sheet used for appointments,
     switches to the "Packages" tab, and adds a row with the first four
     columns: Client Phone, Client Name, Package Code, Language.
  3. The sync job (every 3 minutes) picks up the row, creates the
     package record in packages.json, and writes back: Package ID,
     Purchased At, Expires At, Total Sessions, Sessions Used, Status.
  4. Every booking that uses this package updates Sessions Used on the
     same row, and Status flips to "exhausted" when used up.

Why the sheet and not Telegram or a UI: the clinic's receptionist is
already used to typing into this sheet for appointments; packages live
in the same file, same tab-switching shortcut, zero training.
"""

import logging

import gspread

from services import packages
from services_config import PACKAGES
from services.google_sheets import _gc, _sheet  # reuse authorized client + spreadsheet

logger = logging.getLogger(__name__)

WORKSHEET_NAME = "Packages"

HEADERS = [
    "Client Phone",    # 1 — manual
    "Client Name",     # 2 — manual
    "Package Code",    # 3 — manual (must match a catalog key)
    "Language",        # 4 — manual (en or ar; blank → en)
    "Package ID",      # 5 — auto
    "Purchased At",    # 6 — auto
    "Expires At",      # 7 — auto
    "Total Sessions",  # 8 — auto
    "Sessions Used",   # 9 — auto (updated on every consume/refund)
    "Status",          # 10 — auto (active / exhausted / expired / removed)
]


def _worksheet():
    """Return the Packages worksheet, creating it with headers if missing."""
    spreadsheet = _sheet.spreadsheet
    try:
        ws = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=200, cols=len(HEADERS))
        ws.append_row(HEADERS)
        return ws
    # Self-heal headers if the tab was created but never populated.
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(HEADERS)
    return ws


def _padded(row: list, length: int) -> list:
    return row + [""] * max(0, length - len(row))


def sync_packages() -> dict:
    """Sync the Packages tab with packages.json. Returns a count dict.

    Two-way reconciliation in a single pass:
      - New rows (empty Package ID column) → create package, fill auto cols.
      - Existing rows → update Sessions Used and Status from packages.json.
    """
    ws = _worksheet()
    rows = ws.get_all_values()
    if len(rows) < 1:
        return {"created": 0, "updated": 0, "errors": 0}

    # Index packages.json once so we can look each row up by id.
    all_pkgs = {p["id"]: p for p in packages.list_all()}

    created = 0
    updated = 0
    errors = 0

    for idx, row in enumerate(rows[1:], start=2):  # 1-based row; skip header
        padded = _padded(row, len(HEADERS))
        phone, name, code, lang, pkg_id, _purchased, _expires, _total, _used, _status = padded

        phone = phone.strip()
        name = name.strip()
        code = code.strip()
        lang = (lang.strip() or "en").lower()
        pkg_id = pkg_id.strip()

        # Skip blank rows entirely.
        if not phone and not code and not pkg_id:
            continue

        if not pkg_id:
            # New row — try to create the package.
            if not phone or not code:
                _mark_status(ws, idx, "error: missing phone or code")
                errors += 1
                continue
            if code not in PACKAGES:
                _mark_status(ws, idx, f"error: unknown code {code}")
                errors += 1
                continue
            try:
                pkg = packages.create_package(
                    client_phone=phone,
                    client_name=name or phone,
                    client_mobile=phone,
                    package_code=code,
                    language=lang,
                )
            except Exception as e:
                logger.error("Package sheet create failed row=%s: %s", idx, e)
                _mark_status(ws, idx, f"error: {e}")
                errors += 1
                continue

            ws.update(
                f"E{idx}:J{idx}",
                [[
                    pkg["id"],
                    pkg["purchased_at"],
                    pkg["expires_at"],
                    pkg["total_sessions"],
                    pkg["sessions_used"],
                    "active",
                ]],
            )
            created += 1
            continue

        # Existing row — reconcile Sessions Used and Status from packages.json.
        pkg = all_pkgs.get(pkg_id)
        if not pkg:
            # Row references a package that no longer exists (manually removed).
            if _status != "removed":
                _mark_status(ws, idx, "removed")
                updated += 1
            continue

        new_used = pkg["sessions_used"]
        if pkg["sessions_used"] >= pkg["total_sessions"]:
            new_status = "exhausted"
        else:
            new_status = "active"

        try:
            used_cell_val = int(_used) if str(_used).strip() else -1
        except ValueError:
            used_cell_val = -1

        if used_cell_val != new_used or _status != new_status:
            ws.update(f"I{idx}:J{idx}", [[new_used, new_status]])
            updated += 1

    return {"created": created, "updated": updated, "errors": errors}


def _mark_status(ws, row_idx: int, status: str) -> None:
    """Write a short status string into column J for human debugging."""
    try:
        ws.update_cell(row_idx, 10, status)
    except Exception as e:
        logger.error("Package sheet status write failed row=%s: %s", row_idx, e)
