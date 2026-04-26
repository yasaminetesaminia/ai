import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

import config
from prompts.receptionist import SYSTEM_PROMPT as WHATSAPP_SYSTEM_PROMPT, TOOLS
from prompts.instagram_receptionist import SYSTEM_PROMPT as INSTAGRAM_SYSTEM_PROMPT
from services import google_calendar, google_sheets, packages, waitlist, whatsapp
from services_config import PACKAGES, get_service_duration

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

MAX_HISTORY = 40

# Persian-specific characters that don't appear in Arabic. If the message
# contains any of these, the user wrote (at least partially) in Persian —
# we wrap the input with a hard instruction to reply in Arabic regardless.
# Claude Haiku tends to mirror the input language; prompt rules alone
# weren't enough to stop Persian replies, so we belt-and-suspenders it.
_PERSIAN_ONLY_CHARS = set("پچژگی‌")  # ZWNJ (U+200C) is also in this set


def _looks_persian(text: str) -> bool:
    return any(c in _PERSIAN_ONLY_CHARS for c in (text or ""))


def _wrap_for_persian(text: str) -> str:
    """Prepend a hard instruction so Claude doesn't reply in Persian."""
    return (
        "[SYSTEM NOTE: The next user message contains Persian/Farsi text. "
        "You MUST reply in OMANI ARABIC, never in Persian. "
        "Do not apologize for not speaking Persian — just answer the user's "
        "intent in Arabic naturally, as if they had asked in Arabic.]\n\n"
        f"User wrote: {text}"
    )

_CONV_DIR = Path(__file__).resolve().parent.parent / "conversations"
_CONV_DIR.mkdir(exist_ok=True)

# Keep legacy import name working for any external reference
SYSTEM_PROMPT = WHATSAPP_SYSTEM_PROMPT


_CHANNEL_CONFIG = {
    "whatsapp": {
        "system_prompt": WHATSAPP_SYSTEM_PROMPT,
        "subdir": None,  # legacy: WhatsApp conversations live at conversations/<phone>.json
        "id_label": "WhatsApp ID",
    },
    "instagram": {
        "system_prompt": INSTAGRAM_SYSTEM_PROMPT,
        "subdir": "instagram",
        "id_label": "Instagram User ID",
    },
}


def _conv_path(user_id: str, channel: str = "whatsapp") -> Path:
    safe = "".join(c for c in user_id if c.isalnum())
    subdir = _CHANNEL_CONFIG[channel]["subdir"]
    if subdir:
        base = _CONV_DIR / subdir
        base.mkdir(exist_ok=True)
        return base / f"{safe}.json"
    return _CONV_DIR / f"{safe}.json"


def _load_history(user_id: str, channel: str = "whatsapp") -> list[dict]:
    p = _conv_path(user_id, channel)
    if not p.exists():
        return []
    try:
        history = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    # Self-heal: drop trailing assistant messages that contain orphan tool_use blocks
    # (no matching tool_result in next message). This can happen if the app crashed
    # mid-flow before tool results were appended.
    while history:
        last = history[-1]
        if last.get("role") != "assistant":
            break
        content = last.get("content", [])
        if not isinstance(content, list):
            break
        has_tool_use = any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
        if has_tool_use:
            history.pop()
        else:
            break
    return history


def _normalize_content(content):
    """Convert SDK block objects in content to plain dicts for JSON persistence."""
    if isinstance(content, str):
        return content
    normalized = []
    for block in content:
        if hasattr(block, "model_dump"):
            normalized.append(block.model_dump(mode="json"))
        else:
            normalized.append(block)
    return normalized


def _save_history(user_id: str, history: list[dict], channel: str = "whatsapp") -> None:
    clean = [{"role": m["role"], "content": _normalize_content(m["content"])} for m in history]
    _conv_path(user_id, channel).write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")


def _offer_freed_slot(cancelled_event: dict) -> None:
    """When a booked appointment is cancelled, find the oldest waitlist entry
    waiting on the exact same (department, date, time) and send them a
    WhatsApp heads-up so they can book it themselves.
    """
    # We need the department — it's embedded in the Calendar summary like
    # "[dentistry] checkup - Nazanin". Pull it from the cancelled event.
    import re
    summary = cancelled_event.get("summary", "") or ""
    m = re.match(r"^\[([^\]]+)\]", summary)
    if not m:
        return
    department = m.group(1).strip().lower()
    candidates = waitlist.find_candidates_for_slot(
        department=department,
        date=cancelled_event["date"],
        time=cancelled_event["time"],
    )
    if not candidates:
        return

    winner = candidates[0]
    recipient = winner.get("client_mobile") or winner.get("client_phone")
    if not recipient:
        return

    if winner.get("language") == "ar":
        msg = (
            f"مرحباً {winner['client_name']} 👋\n\n"
            f"فتح موعد كنت تنتظره في {config.BUSINESS_NAME}:\n"
            f"📅 {winner['desired_date']} 🕐 {winner['desired_time']}\n\n"
            f"راسلنا هنا بكلمة «أحجز» للحجز قبل ما يأخذه غيرك."
        )
    else:
        msg = (
            f"Hi {winner['client_name']} 👋\n\n"
            f"A slot you were waiting for at {config.BUSINESS_NAME} just opened up:\n"
            f"📅 {winner['desired_date']} 🕐 {winner['desired_time']}\n\n"
            f"Reply with 'book' here to grab it before someone else does."
        )

    try:
        whatsapp.send_message(recipient, msg)
        waitlist.remove_entry(
            client_phone=winner["client_phone"],
            department=winner["department"],
            date=winner["desired_date"],
            time=winner["desired_time"],
        )
    except Exception:
        # Swallow — a delivery failure shouldn't unwind the cancel.
        pass


def _execute_tool(tool_name: str, tool_input: dict, channel: str = "whatsapp") -> str:
    """Execute a tool call and return the result as a string."""
    if tool_name == "check_available_slots":
        slots = google_calendar.get_available_slots(
            date_str=tool_input["date"],
            department=tool_input["department"],
            sub_service=tool_input["sub_service"],
            units=tool_input.get("units", 1),
            doctor=tool_input.get("doctor"),
        )
        if slots:
            return json.dumps({
                "available_slots": slots[:15],
                "total_available": len(slots),
                "date": tool_input["date"],
                "department": tool_input["department"],
            })
        return json.dumps({
            "message": "No available slots on this date for this department/service.",
            "date": tool_input["date"],
        })

    elif tool_name == "book_appointment":
        package_id = tool_input.get("package_id")
        # If AI supplied a package_id, consume a session BEFORE booking so a
        # failed calendar insert doesn't eat a session. If the calendar book
        # raises, we refund.
        consumed_pkg = None
        if package_id:
            consumed_pkg = packages.consume_session(package_id)
            if not consumed_pkg:
                return json.dumps({
                    "success": False,
                    "package_unavailable": True,
                    "message": "That package has no remaining sessions or is expired. Book without package.",
                })
        try:
            result = google_calendar.book_appointment(
                client_name=tool_input["client_name"],
                client_phone=tool_input["client_phone"],
                department=tool_input["department"],
                sub_service=tool_input["sub_service"],
                date_str=tool_input["date"],
                time_str=tool_input["time"],
                duration_minutes=tool_input["duration_minutes"],
                doctor=tool_input.get("doctor"),
                channel=channel,
                language=tool_input.get("language", "en"),
                client_mobile=tool_input.get("client_mobile"),
                package_id=package_id,
            )
            if consumed_pkg:
                packages.notify_if_exhausted(consumed_pkg)
                result["sessions_remaining"] = packages.sessions_remaining(consumed_pkg)
            return json.dumps({"success": True, **result})
        except google_calendar.SlotNoLongerAvailable:
            if consumed_pkg:
                packages.refund_session(package_id)
            return json.dumps({
                "success": False,
                "slot_taken": True,
                "message": "That slot was just taken by another client. Ask the client to pick a different time — run check_available_slots again.",
            })

    elif tool_name == "cancel_appointment":
        cancelled = google_calendar.cancel_appointment(tool_input["client_phone"])
        if cancelled:
            google_sheets.delete_appointment(
                phone=cancelled["mobile"],
                date=cancelled["date"],
                time=cancelled["time"],
            )
            # Refund any package session that was consumed for this booking.
            if cancelled.get("package_id"):
                packages.refund_session(cancelled["package_id"])
            # Also clear any waitlist entries the cancelling client had.
            waitlist.remove_by_client(tool_input["client_phone"])
            # Notify the next person on the waitlist for this freed slot.
            _offer_freed_slot(cancelled)
            return json.dumps({"success": True, "message": "Appointment cancelled."})
        return json.dumps({"success": False, "message": "No upcoming appointment found."})

    elif tool_name == "reschedule_appointment":
        result = google_calendar.reschedule_appointment(
            client_phone=tool_input["client_phone"],
            new_date=tool_input["new_date"],
            new_time=tool_input["new_time"],
        )
        if result:
            google_sheets.update_appointment_time(
                phone=result["mobile"],
                old_date=result["old_date"],
                old_time=result["old_time"],
                new_date=result["new_date"],
                new_time=result["new_time"],
            )
            return json.dumps({"success": True, **result})
        return json.dumps({"success": False, "message": "No upcoming appointment found."})

    elif tool_name == "get_my_appointment":
        appointment = google_calendar.get_client_appointment(tool_input["client_phone"])
        if appointment:
            return json.dumps({"found": True, **appointment})
        return json.dumps({"found": False, "message": "No upcoming appointment found."})

    elif tool_name == "add_to_waitlist":
        waitlist.add_entry(
            client_phone=tool_input["client_phone"],
            client_name=tool_input["client_name"],
            client_mobile=tool_input.get("client_mobile") or tool_input["client_phone"],
            department=tool_input["department"],
            sub_service=tool_input["sub_service"],
            desired_date=tool_input["desired_date"],
            desired_time=tool_input["desired_time"],
            duration_minutes=tool_input["duration_minutes"],
            channel=channel,
            language=tool_input.get("language", "en"),
            doctor=tool_input.get("doctor"),
        )
        return json.dumps({
            "success": True,
            "message": "Added to waitlist. We'll message you if this slot opens up.",
        })

    elif tool_name == "remove_from_waitlist":
        removed = waitlist.remove_by_client(tool_input["client_phone"])
        return json.dumps({"success": True, "removed": removed})

    elif tool_name == "list_my_packages":
        active = packages.get_active_packages(tool_input["client_phone"])
        if not active:
            return json.dumps({"has_packages": False, "packages": []})
        summarized = [
            {
                "package_id": p["id"],
                "name_en": PACKAGES.get(p["package_code"], {}).get("name_en", p["package_code"]),
                "name_ar": PACKAGES.get(p["package_code"], {}).get("name_ar", p["package_code"]),
                "department": p["department"],
                "sub_service": p["sub_service"],
                "sessions_remaining": packages.sessions_remaining(p),
                "total_sessions": p["total_sessions"],
                "expires_at": p.get("expires_at"),
            }
            for p in active
        ]
        return json.dumps({"has_packages": True, "packages": summarized})

    elif tool_name == "list_package_catalog":
        catalog = [
            {
                "code": code,
                "name_en": pkg["name_en"],
                "name_ar": pkg["name_ar"],
                "department": pkg["department"],
                "sub_service": pkg["sub_service"],
                "total_sessions": pkg["total_sessions"],
                "price_omr": pkg["price_omr"],
                "regular_price_omr": pkg["regular_price_omr"],
                "validity_months": pkg["validity_months"],
            }
            for code, pkg in PACKAGES.items()
        ]
        return json.dumps({"catalog": catalog})

    elif tool_name == "save_client_to_sheet":
        # Voice-agent Claude sometimes omits `is_new_client` — default to True
        # so the call doesn't crash. Same for missing doctor/mobile.
        google_sheets.add_client(
            client_name=tool_input["client_name"],
            client_phone=tool_input["client_phone"],
            department=tool_input["department"],
            sub_service=tool_input["sub_service"],
            doctor=tool_input.get("doctor", ""),
            appointment_date=tool_input["appointment_date"],
            appointment_time=tool_input["appointment_time"],
            is_new_client=tool_input.get("is_new_client", True),
            client_mobile=tool_input.get("client_mobile"),
        )
        return json.dumps({"success": True, "message": "Client saved to sheet."})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


def handle_message(user_id: str, text: str, channel: str = "whatsapp") -> str:
    """Process a message from a client and return the AI response.

    `user_id` is the channel-specific unique ID:
      - WhatsApp: the phone number
      - Instagram: the IGSID (Instagram-scoped user ID)
    It's passed as `client_phone` in all tool calls (calendar/sheets use it as the lookup key).
    """
    channel_cfg = _CHANNEL_CONFIG[channel]
    system_prompt = channel_cfg["system_prompt"]
    id_label = channel_cfg["id_label"]

    history = _load_history(user_id, channel)
    # If the user wrote in Persian, wrap with a hard "reply in Arabic"
    # instruction so Claude doesn't drift into a Persian reply.
    user_msg = _wrap_for_persian(text) if _looks_persian(text) else text
    history.append({"role": "user", "content": user_msg})

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    now = datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE))
    today_str = now.strftime("%Y-%m-%d")
    day_name = now.strftime("%A")
    time_str = now.strftime("%H:%M")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    is_friday = day_name.lower() == config.BUSINESS_CLOSED_DAY.lower()
    is_holiday_today = today_str in config.BUSINESS_HOLIDAYS
    upcoming_holidays = [h for h in config.BUSINESS_HOLIDAYS if h >= today_str][:5]

    status_line = ""
    if is_friday:
        status_line = "⚠️ Clinic is CLOSED today (Friday)."
    elif is_holiday_today:
        status_line = "⚠️ Clinic is CLOSED today (public holiday)."

    context_block = (
        f"\n## Current Context (authoritative — use instead of asking the client)\n"
        f"- Today: **{today_str}** ({day_name})\n"
        f"- Current time: **{time_str}** ({config.BUSINESS_TIMEZONE})\n"
        f"- Tomorrow: {tomorrow_str}\n"
        f"- Upcoming closed dates (holidays): {', '.join(upcoming_holidays) if upcoming_holidays else 'none listed'}\n"
        f"{status_line}\n"
        f"\n## Current Client\n"
        f"{id_label} (use as `client_phone` in ALL tool calls): {user_id}"
    )

    cached_system = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": context_block},
    ]

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=cached_system,
        tools=TOOLS,
        messages=history,
    )

    # Process response — loop while any tool_use block is present (don't rely on stop_reason alone)
    while any(getattr(b, "type", None) == "tool_use" for b in response.content):
        assistant_content = response.content
        history.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                result = _execute_tool(block.name, block.input, channel)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        history.append({"role": "user", "content": tool_results})

        response = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=cached_system,
            tools=TOOLS,
            messages=history,
        )

    # Extract final text response
    assistant_content = response.content
    history.append({"role": "assistant", "content": assistant_content})

    reply_text = ""
    for block in assistant_content:
        if hasattr(block, "text"):
            reply_text += block.text

    _save_history(user_id, history, channel)
    return reply_text
