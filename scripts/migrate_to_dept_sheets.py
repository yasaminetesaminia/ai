"""One-time migration from the legacy single sheet1 → per-department tabs.

Reads every booking from the original sheet1, classifies it by department,
and re-inserts it into the right new tab (Dentistry / Laser / Slimming /
Beauty). After successful migration, the legacy rows are kept in place
so nothing is destroyed — review the new tabs, then manually delete the
legacy rows or rename sheet1 to "Legacy" if you want it out of the way.

Run from the repo root:
    python scripts/migrate_to_dept_sheets.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import google_sheets  # noqa: E402
from services_config import DEPARTMENT_SHEET_NAMES  # noqa: E402


def _normalize_dept(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if s in DEPARTMENT_SHEET_NAMES:
        return s
    if "dent" in s:
        return "dentistry"
    if "laser" in s:
        return "laser_hair_removal"
    if "slim" in s:
        return "slimming"
    if "beauty" in s or "aesthetic" in s:
        return "beauty"
    return None


def main() -> None:
    legacy = google_sheets._sheet  # the original sheet1
    rows = legacy.get_all_values()
    if len(rows) <= 1:
        print("Legacy sheet1 has no data. Nothing to migrate.")
        return

    header = rows[0]
    data = rows[1:]
    print(f"Found {len(data)} legacy rows in sheet1. Header: {header}")
    print()

    # Skip rows that don't look like booking rows.
    migrated = 0
    skipped = 0
    by_dept: dict[str, int] = {}

    for row in data:
        padded = row + [""] * (len(google_sheets.HEADERS) - len(row))
        name, phone, dept_raw, service, doctor, appt_date, appt_time, new_client, _registered = padded[:9]

        dept_key = _normalize_dept(dept_raw)
        if not dept_key or not name or not appt_date:
            skipped += 1
            continue

        # Use the public API so the row is sorted into place correctly.
        google_sheets.add_client(
            client_name=name,
            client_phone=phone,
            department=dept_key,
            sub_service=service,
            doctor=doctor,
            appointment_date=appt_date,
            appointment_time=appt_time,
            is_new_client=(new_client.strip().lower() == "yes"),
            client_mobile=phone,
        )
        migrated += 1
        by_dept[dept_key] = by_dept.get(dept_key, 0) + 1

    print(f"Migrated {migrated} rows.")
    print(f"Skipped  {skipped} rows (missing dept/name/date).")
    print()
    for dept, n in sorted(by_dept.items(), key=lambda x: -x[1]):
        tab = DEPARTMENT_SHEET_NAMES.get(dept, dept)
        print(f"  → {tab}: {n}")
    print()
    print("Legacy rows in sheet1 were NOT deleted. Review the new tabs,")
    print("then manually clear or rename sheet1 once you're satisfied.")


if __name__ == "__main__":
    main()
