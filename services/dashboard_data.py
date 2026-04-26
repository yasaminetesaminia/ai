"""Aggregate clinic data for the admin dashboard.

The dashboard view is read-only — every helper here returns plain dicts /
lists ready for templating. We deliberately avoid hitting Google APIs in
the request path on every refresh (slow, quota-burning, also throws
transient SSL errors). Strategy:

  - 30-second in-memory cache for Google Calendar data — multiple widget
    refreshes on the page reuse the same fetched data
  - Every Google API call is wrapped: on failure we log a warning (not
    error) and return the last good result, OR an empty list if there
    isn't one. This way a momentary network blip doesn't spam the
    clinic's Telegram alerts.
  - Local files (waitlist, packages, conversation history) are read
    fresh every time — they're cheap and always available.
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from services import google_calendar, packages, waitlist
from services_config import SERVICES, get_service_price

logger = logging.getLogger(__name__)

_CONV_DIR = Path(__file__).resolve().parent.parent / "conversations"
_TZ = ZoneInfo(config.BUSINESS_TIMEZONE)

# Module-level cache: {key: (timestamp, value)}
# Both upcoming-24h and upcoming-7d are cached separately, but each at
# the same TTL so widgets refreshing at different intervals all see a
# consistent snapshot. 30s is short enough that newly-booked
# appointments show up quickly, long enough to absorb the burst of
# requests when all widgets refresh in parallel.
_CACHE_TTL_SECONDS = 30
_cache: dict = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(_TZ)


def _cached_calendar_fetch(key: str, hours_ahead: int) -> list[dict]:
    """Wrap google_calendar.get_upcoming_appointments with caching +
    swallow-and-log error handling. Returns last good result on failure
    so the dashboard stays usable through transient network blips.
    """
    now_ts = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now_ts - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    try:
        data = google_calendar.get_upcoming_appointments(hours_ahead=hours_ahead)
    except Exception as e:
        # Log as warning (not error) so it doesn't trip the Telegram
        # alerts handler — these are typically transient SSL/DNS hiccups
        # that resolve on the next call. If we have any cached data,
        # serve it; otherwise serve empty so the page still renders.
        logger.warning("Dashboard calendar fetch failed (%s): %s", key, e)
        with _cache_lock:
            cached = _cache.get(key)
        return cached[1] if cached else []

    with _cache_lock:
        _cache[key] = (now_ts, data)
    return data


# ---------------------------------------------------------------------------
# Appointments (today / week / breakdown)
# ---------------------------------------------------------------------------
def appointments_today() -> list[dict]:
    """Today's confirmed appointments, oldest-first."""
    appts = _cached_calendar_fetch("today_24h", hours_ahead=24)
    today_str = _now().strftime("%Y-%m-%d")
    todays = [a for a in appts if a["date"] == today_str]
    todays.sort(key=lambda a: a["time"])
    return todays


def appointments_next_7_days() -> list[dict]:
    """All appointments in the upcoming 7 days."""
    return _cached_calendar_fetch("upcoming_7d", hours_ahead=24 * 7)


def appointments_grouped_by_day(days: int) -> list[dict]:
    """Return appointments grouped by date for the next `days` days.

    Output: [{"date", "day_name", "date_label", "appointments": [...]}]
    Days with no appointments are still included so the calendar feels
    continuous; the template can choose whether to show them.
    """
    appts = _cached_calendar_fetch(f"upcoming_{days}d", hours_ahead=24 * days)

    # Index by date for fast lookup.
    by_date: dict[str, list[dict]] = {}
    for a in appts:
        by_date.setdefault(a["date"], []).append(a)
    for date_appts in by_date.values():
        date_appts.sort(key=lambda x: x["time"])

    out = []
    today = _now()
    for i in range(days):
        d = today + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        out.append({
            "date": date_str,
            "day_name": d.strftime("%A"),    # Monday, Tuesday, ...
            "day_short": d.strftime("%a"),    # Mon, Tue, ...
            "date_label": d.strftime("%b %d"),  # Apr 25
            "is_today": (i == 0),
            "is_friday": d.strftime("%A").lower() == config.BUSINESS_CLOSED_DAY.lower(),
            "appointments": by_date.get(date_str, []),
        })
    return out


def appointments_per_day(days: int = 7) -> list[dict]:
    """[{date, day_name, count}] for the next N days — useful for a bar chart."""
    appts = _cached_calendar_fetch(f"upcoming_{days}d", hours_ahead=24 * days)
    by_date: dict[str, int] = {}
    for a in appts:
        by_date[a["date"]] = by_date.get(a["date"], 0) + 1

    out = []
    today = _now()
    for i in range(days):
        d = today + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        out.append({
            "date": date_str,
            "day_name": d.strftime("%a"),  # Mon, Tue, ...
            "count": by_date.get(date_str, 0),
        })
    return out


def department_breakdown_today() -> list[dict]:
    """Counts of today's appointments per department."""
    todays = appointments_today()
    counts: dict[str, int] = {}
    for a in todays:
        # Calendar summary is "[department] sub_service - Name (Doctor)"
        summary = a.get("summary", "")
        if summary.startswith("[") and "]" in summary:
            dept = summary[1:summary.index("]")]
            counts[dept] = counts.get(dept, 0) + 1

    out = []
    for dept_key, dept in SERVICES.items():
        out.append({
            "key": dept_key,
            "name": dept["name"],
            "name_ar": dept.get("name_ar", ""),
            "count": counts.get(dept_key, 0),
        })
    return out


# ---------------------------------------------------------------------------
# Conversations (recent across channels)
# ---------------------------------------------------------------------------
def _scan_conv_dir(subdir: str | None) -> list[Path]:
    base = _CONV_DIR / subdir if subdir else _CONV_DIR
    if not base.exists():
        return []
    return [p for p in base.glob("*.json") if p.is_file()]


def _last_message_preview(history: list, max_len: int = 80) -> str:
    """Pull the last user/assistant text message and truncate."""
    for entry in reversed(history):
        content = entry.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        else:
            text = ""
        if text:
            text = text.replace("\n", " ").strip()
            return text[:max_len] + ("…" if len(text) > max_len else "")
    return "(no messages)"


def recent_conversations(limit: int = 10) -> list[dict]:
    """Last `limit` conversations across WhatsApp / Instagram / Voice, sorted
    by most-recently-touched.
    """
    items: list[dict] = []
    sources = [
        ("whatsapp", None),
        ("instagram", "instagram"),
        ("voice", "voice"),
    ]
    excluded_files = {
        "waitlist.json", "packages.json", "retention_sent.json",
        "sync_state.json", "pronunciation_state.json",
    }

    for channel, subdir in sources:
        for path in _scan_conv_dir(subdir):
            if path.name in excluded_files:
                continue
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(history, list) or not history:
                    continue
            except Exception:
                continue

            mtime = path.stat().st_mtime
            user_id = path.stem
            preview = _last_message_preview(history)
            items.append({
                "channel": channel,
                "user_id": user_id,
                "preview": preview,
                "message_count": len(history),
                "mtime": mtime,
                "last_seen": datetime.fromtimestamp(mtime, _TZ).strftime("%Y-%m-%d %H:%M"),
            })

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
def active_packages_summary() -> list[dict]:
    """Active (non-expired, non-empty) packages across all clients."""
    all_pkgs = packages.list_all()
    today = _now().date()
    summarized = []
    for p in all_pkgs:
        # Skip expired or fully used.
        try:
            from datetime import date
            expires = date.fromisoformat(p.get("expires_at", "9999-12-31"))
        except Exception:
            expires = None
        used = p.get("sessions_used", 0)
        total = p.get("total_sessions", 0)
        if used >= total:
            continue
        if expires and expires < today:
            continue

        catalog = SERVICES.get(p["department"], {})
        summarized.append({
            "client_name": p.get("client_name", ""),
            "client_phone": p.get("client_phone", ""),
            "department": p["department"],
            "department_name": catalog.get("name", p["department"]),
            "sub_service": p.get("sub_service", ""),
            "sessions_used": used,
            "total_sessions": total,
            "remaining": total - used,
            "expires_at": p.get("expires_at", ""),
        })
    summarized.sort(key=lambda x: x.get("expires_at") or "9999-12-31")
    return summarized


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------
def waitlist_summary() -> list[dict]:
    entries = waitlist.list_all()
    out = []
    for e in entries:
        catalog = SERVICES.get(e.get("department", ""), {})
        out.append({
            "client_name": e.get("client_name", ""),
            "client_mobile": e.get("client_mobile", ""),
            "department": e.get("department", ""),
            "department_name": catalog.get("name", e.get("department", "")),
            "sub_service": e.get("sub_service", ""),
            "desired_date": e.get("desired_date", ""),
            "desired_time": e.get("desired_time", ""),
            "added_at": e.get("added_at", ""),
        })
    return out


# ---------------------------------------------------------------------------
# High-level KPIs (the headline numbers)
# ---------------------------------------------------------------------------
def kpis() -> dict:
    """Single dict of headline metrics for the top of the dashboard."""
    today = appointments_today()
    week = appointments_next_7_days()
    pkgs = active_packages_summary()
    wl = waitlist_summary()

    # Count conversation files = unique clients ever served.
    total_clients = (
        len(_scan_conv_dir(None))
        + len(_scan_conv_dir("instagram"))
        + len(_scan_conv_dir("voice"))
    )

    return {
        "today_count": len(today),
        "week_count": len(week),
        "active_packages": len(pkgs),
        "waitlist_count": len(wl),
        "total_clients": total_clients,
    }


# ---------------------------------------------------------------------------
# Today's revenue + week-ahead revenue
# ---------------------------------------------------------------------------
def revenue_for(appts: list[dict]) -> dict:
    """Sum the catalogue prices of a list of appointments. Per-tooth
    veneer is approximated as a single tooth — manager can adjust if needed.
    """
    total = 0.0
    priced = 0
    unpriced = 0
    by_dept: dict[str, float] = {}
    for a in appts:
        dept = a.get("department", "")
        sub = a.get("sub_service", "")
        price_info = get_service_price(dept, sub)
        if not price_info:
            unpriced += 1
            continue
        total += price_info["omr"]
        priced += 1
        by_dept[dept] = by_dept.get(dept, 0) + price_info["omr"]
    return {
        "omr": int(round(total)),
        "appointment_count": len(appts),
        "priced_count": priced,
        "unpriced_count": unpriced,
        "by_department": by_dept,
    }


def revenue_today() -> dict:
    return revenue_for(appointments_today())


def revenue_this_week() -> dict:
    return revenue_for(appointments_next_7_days())
