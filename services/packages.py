"""Pre-paid session packages (multi-session bundles at a discount).

Typical flow:
  1. Client asks about a laser/slimming/botox package. Claude lists the
     catalog entries (from services_config.PACKAGES) and prices.
  2. Client pays at reception. Receptionist runs `/package_add <phone> <code>`
     in Telegram → `create_package` writes a record to packages.json.
  3. Next time the client books a matching service, Claude calls
     `find_usable_package` and — with the client's consent — passes the
     resulting `package_id` to `book_appointment`, which invokes
     `consume_session` and stamps the package_id on the Calendar event.
  4. On cancel we refund the session (`refund_session`). On the last
     session used, the retention job can pick up the "empty package" case
     and offer a renewal.

State: conversations/packages.json (JSON list of package dicts).
Thread-safety: all mutating ops go through `_lock`.
"""

import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services_config import PACKAGES

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conversations",
    "packages.json",
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


def _save(packages: list[dict]) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(packages, f, ensure_ascii=False, indent=2)


def _is_expired(pkg: dict, today: date | None = None) -> bool:
    today = today or datetime.now().date()
    expires = pkg.get("expires_at")
    if not expires:
        return False
    try:
        return date.fromisoformat(expires) < today
    except ValueError:
        return False


def _matches_service(pkg: dict, department: str, sub_service: str) -> bool:
    if pkg["department"] != department:
        return False
    if pkg["sub_service"] == "_any_":
        return True
    return pkg["sub_service"] == sub_service


def create_package(
    client_phone: str,
    client_name: str,
    client_mobile: str,
    package_code: str,
    language: str = "en",
) -> dict:
    """Register a pre-paid package for a client. Raises ValueError if the
    code isn't in the catalog.
    """
    catalog = PACKAGES.get(package_code)
    if not catalog:
        raise ValueError(f"Unknown package code: {package_code}")

    now = datetime.now(timezone.utc)
    expires_date = (now + timedelta(days=30 * catalog["validity_months"])).date()

    pkg: dict[str, Any] = {
        "id": f"pkg-{uuid.uuid4().hex[:12]}",
        "client_phone": client_phone,
        "client_name": client_name,
        "client_mobile": client_mobile,
        "language": language,
        "package_code": package_code,
        "department": catalog["department"],
        "sub_service": catalog["sub_service"],
        "total_sessions": catalog["total_sessions"],
        "sessions_used": 0,
        "price_paid_omr": catalog["price_omr"],
        "purchased_at": now.isoformat(),
        "expires_at": expires_date.isoformat(),
    }

    with _lock:
        packages = _load()
        packages.append(pkg)
        _save(packages)
    return pkg


def get_active_packages(client_phone: str) -> list[dict]:
    """All non-expired packages for this client with remaining sessions."""
    packages = _load()
    today = datetime.now().date()
    return [
        p for p in packages
        if p["client_phone"] == client_phone
        and p["sessions_used"] < p["total_sessions"]
        and not _is_expired(p, today)
    ]


def find_usable_package(
    client_phone: str, department: str, sub_service: str
) -> dict | None:
    """First active package that matches this service, or None. When multiple
    match, prefer the one that expires soonest (so the client doesn't lose it).
    """
    candidates = [
        p for p in get_active_packages(client_phone)
        if _matches_service(p, department, sub_service)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.get("expires_at") or "9999-12-31")
    return candidates[0]


def consume_session(package_id: str) -> dict | None:
    """Decrement remaining sessions on a package. Returns the updated package,
    or None if not found / already empty / expired.
    """
    with _lock:
        packages = _load()
        for p in packages:
            if p["id"] != package_id:
                continue
            if p["sessions_used"] >= p["total_sessions"]:
                return None
            if _is_expired(p):
                return None
            p["sessions_used"] += 1
            _save(packages)
            return p
    return None


def refund_session(package_id: str) -> dict | None:
    """Restore one session after a cancellation. No-op if the package is
    unknown or already at zero uses.
    """
    with _lock:
        packages = _load()
        for p in packages:
            if p["id"] != package_id:
                continue
            if p["sessions_used"] <= 0:
                return p
            p["sessions_used"] -= 1
            _save(packages)
            return p
    return None


def remove_package(package_id: str) -> bool:
    """Hard-delete a package (e.g., refund + remove on refund request)."""
    with _lock:
        packages = _load()
        kept = [p for p in packages if p["id"] != package_id]
        if len(kept) == len(packages):
            return False
        _save(kept)
    return True


def list_all() -> list[dict]:
    return _load()


def sessions_remaining(pkg: dict) -> int:
    return pkg["total_sessions"] - pkg["sessions_used"]


def package_display_name(pkg: dict) -> str:
    """Localized display name looked up from the catalog, falling back to code."""
    catalog = PACKAGES.get(pkg.get("package_code") or "", {})
    if pkg.get("language") == "ar":
        return catalog.get("name_ar") or pkg.get("package_code", "")
    return catalog.get("name_en") or pkg.get("package_code", "")


def notify_if_exhausted(pkg: dict) -> None:
    """If the package just hit zero remaining sessions, WhatsApp the client
    and suggest renewing. Safe to call after every `consume_session` — it's
    a no-op unless `sessions_used == total_sessions`.
    """
    if sessions_remaining(pkg) != 0:
        return

    # Deferred import to avoid a circular import at module load time.
    from services import whatsapp
    import config

    recipient = pkg.get("client_mobile") or pkg.get("client_phone")
    if not recipient:
        return

    name = pkg.get("client_name") or ""
    display = package_display_name(pkg)

    if pkg.get("language") == "ar":
        msg = (
            f"مرحباً {name}! 🌟\n\n"
            f"هذه كانت آخر جلسة من باقتك ({display}) في {config.BUSINESS_NAME}. "
            f"للاستمرار في النتائج الرائعة، هل تودين تجديد الباقة؟ فقط أرسلي هنا."
        )
    else:
        msg = (
            f"Hi {name}! 🌟\n\n"
            f"That was the last session from your {display} package at "
            f"{config.BUSINESS_NAME}. To keep the results going, would you "
            f"like to renew? Just reply here."
        )

    try:
        whatsapp.send_message(recipient, msg)
    except Exception as e:
        logger.error("Package-exhausted notify failed for %s: %s", recipient, e)
