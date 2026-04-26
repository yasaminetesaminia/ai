"""Simple waitlist stored as a JSON file.

When a client wants a slot that's already full, Claude calls `add_entry`.
When an appointment is cancelled, `find_candidates_for_slot` returns the
entries that were waiting on that (department, date, time) so the caller
can notify them via WhatsApp.

We store by `client_phone` (the lookup ID — WhatsApp number or Instagram IGSID)
so a client can also cancel their own waitlist entry from chat.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conversations",
    "waitlist.json",
)
_lock = threading.Lock()


def _load() -> list[dict]:
    if not os.path.exists(_STATE_FILE):
        return []
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def add_entry(
    client_phone: str,
    client_name: str,
    client_mobile: str,
    department: str,
    sub_service: str,
    desired_date: str,
    desired_time: str,
    duration_minutes: int,
    channel: str = "whatsapp",
    language: str = "en",
    doctor: str | None = None,
) -> dict:
    """Add a client to the waitlist for a specific slot. Idempotent — a client
    can have at most one entry per (department, date, time)."""
    entry: dict[str, Any] = {
        "client_phone": client_phone,
        "client_name": client_name,
        "client_mobile": client_mobile,
        "department": department,
        "sub_service": sub_service,
        "desired_date": desired_date,
        "desired_time": desired_time,
        "duration_minutes": duration_minutes,
        "channel": channel,
        "language": language,
        "doctor": doctor,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        entries = _load()
        # Drop any prior entry by the same client for the same slot.
        entries = [
            e for e in entries
            if not (
                e["client_phone"] == client_phone
                and e["department"] == department
                and e["desired_date"] == desired_date
                and e["desired_time"] == desired_time
            )
        ]
        entries.append(entry)
        _save(entries)
    return entry


def remove_by_client(client_phone: str) -> int:
    """Remove all waitlist entries for a given client. Returns count removed."""
    with _lock:
        entries = _load()
        kept = [e for e in entries if e["client_phone"] != client_phone]
        removed = len(entries) - len(kept)
        if removed:
            _save(kept)
    return removed


def find_candidates_for_slot(department: str, date: str, time: str) -> list[dict]:
    """Return waitlist entries matching a freed-up slot, oldest-first."""
    with _lock:
        entries = _load()
    candidates = [
        e for e in entries
        if e["department"] == department
        and e["desired_date"] == date
        and e["desired_time"] == time
    ]
    candidates.sort(key=lambda e: e.get("added_at", ""))
    return candidates


def remove_entry(
    client_phone: str, department: str, date: str, time: str
) -> bool:
    """Remove a single waitlist entry after it's been offered to the client."""
    with _lock:
        entries = _load()
        kept = [
            e for e in entries
            if not (
                e["client_phone"] == client_phone
                and e["department"] == department
                and e["desired_date"] == date
                and e["desired_time"] == time
            )
        ]
        changed = len(kept) != len(entries)
        if changed:
            _save(kept)
    return changed


def list_all() -> list[dict]:
    return _load()
