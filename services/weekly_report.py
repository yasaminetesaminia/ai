import io
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)

import config
from services import telegram, google_sheets

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_credentials = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIALS_FILE, scopes=_SCOPES
)
_service = build("calendar", "v3", credentials=_credentials)
_tz = ZoneInfo(config.BUSINESS_TIMEZONE)

# Department key → display label
DEPARTMENT_LABELS = {
    "dentistry": "Dentistry",
    "laser_hair_removal": "Laser Hair Removal",
    "slimming": "Slimming",
    "beauty": "Beauty & Aesthetics",
}

# Normalize department values written however-they-were-typed (by bot or human
# in the Sheet) back to the canonical key used in DEPARTMENT_LABELS.
def _normalize_department(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if s in DEPARTMENT_LABELS:
        return s
    # common Sheet variants typed by the human receptionist
    if "dent" in s:
        return "dentistry"
    if "laser" in s:
        return "laser_hair_removal"
    if "slim" in s:
        return "slimming"
    if "beauty" in s or "aesthetic" in s:
        return "beauty"
    return None


# Pattern matching the summary format: "[department] sub_service - client_name (...)"
_SUMMARY_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")


def _parse_description(description: str) -> dict:
    out = {}
    for line in description.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip().lower()] = value.strip()
    return out


def _next_week_range(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of the next working week.

    Oman working week is Saturday → Thursday (Friday closed). From the current
    moment, this returns the next Saturday 00:00 through the following Friday
    00:00 (exclusive end).
    """
    # weekday(): Mon=0 ... Sat=5, Sun=6
    days_until_saturday = (5 - now.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7  # if today is Saturday, jump to next Saturday
    start = (now + timedelta(days=days_until_saturday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7)
    return start, end


def _fetch_events(start: datetime, end: datetime) -> list[dict]:
    result = _service.events().list(
        calendarId=config.GOOGLE_CALENDAR_ID,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=500,
    ).execute()
    return result.get("items", [])


def _calendar_appointments(events: list[dict]) -> list[dict]:
    """Extract unified appointment dicts from raw Calendar events."""
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
        out.append({
            "department": dept,
            "start": start_dt,
            "end": end_dt,
            "sub_service": m.group(2).strip(),
            "client_name": desc.get("client", ""),
            "mobile": desc.get("mobile", desc.get("phone", "")),
            "channel": desc.get("channel", "whatsapp"),
            "doctor": desc.get("doctor", ""),
            "duration": desc.get("duration", ""),
            "source": "calendar",
        })
    return out


def _sheet_appointments(start_date: str, end_date: str) -> list[dict]:
    """Extract unified appointment dicts from Sheet rows in [start_date, end_date)."""
    out = []
    for row in google_sheets.get_appointments_in_range(start_date, end_date):
        dept = _normalize_department(row["department"])
        if not dept:
            continue
        try:
            start_dt = datetime.strptime(
                f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=_tz)
        except ValueError:
            continue
        out.append({
            "department": dept,
            "start": start_dt,
            "end": None,
            "sub_service": row["service"],
            "client_name": row["name"],
            "mobile": row["phone"],
            "channel": "manual",
            "doctor": row["doctor"],
            "duration": "",
            "source": "sheet",
        })
    return out


def _merge_and_group(
    calendar_appts: list[dict], sheet_appts: list[dict]
) -> dict[str, list[dict]]:
    """Dedupe (calendar wins) on (department + date + time + normalized name) and group by department."""
    seen: set[tuple] = set()
    merged: list[dict] = []

    def key(a: dict) -> tuple:
        name_norm = (a.get("client_name") or "").strip().lower()
        return (a["department"], a["start"].strftime("%Y-%m-%d %H:%M"), name_norm)

    for a in calendar_appts:
        k = key(a)
        if k in seen:
            continue
        seen.add(k)
        merged.append(a)

    for a in sheet_appts:
        k = key(a)
        if k in seen:
            continue
        seen.add(k)
        merged.append(a)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in merged:
        grouped[a["department"]].append(a)
    for dept in grouped:
        grouped[dept].sort(key=lambda x: x["start"])
    return grouped


_TABLE_HEADERS = [
    "#", "Day", "Date", "Time", "Client", "Mobile",
    "Service", "Doctor", "Duration", "Channel",
]


def _build_department_pdf(
    dept_key: str,
    appts: list[dict],
    week_start: datetime,
    week_end: datetime,
) -> bytes:
    """Render a single-department PDF (A4 landscape) with a details table."""
    label = DEPARTMENT_LABELS[dept_key]
    week_end_inclusive = week_end - timedelta(days=1)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{config.BUSINESS_NAME} - {label}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleBig", parent=styles["Title"], fontSize=18, spaceAfter=6, alignment=1
    )
    subtitle_style = ParagraphStyle(
        "Sub", parent=styles["Normal"], fontSize=11, alignment=1, textColor=colors.HexColor("#444444")
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"], fontSize=9, leading=11, wordWrap="CJK"
    )

    story = [
        Paragraph(f"{config.BUSINESS_NAME} — {label}", title_style),
        Paragraph(
            f"Week: {week_start.strftime('%a %Y-%m-%d')} → "
            f"{week_end_inclusive.strftime('%a %Y-%m-%d')} &nbsp;|&nbsp; "
            f"Total appointments: <b>{len(appts)}</b>",
            subtitle_style,
        ),
        Spacer(1, 8),
    ]

    if not appts:
        story.append(
            Paragraph(
                "<para alignment='center'><i>No appointments booked for this week.</i></para>",
                styles["Normal"],
            )
        )
    else:
        data = [_TABLE_HEADERS]
        for i, a in enumerate(appts, start=1):
            start_s = a["start"].strftime("%H:%M")
            end_s = a["end"].strftime("%H:%M") if a.get("end") else "-"
            data.append([
                Paragraph(str(i), cell_style),
                Paragraph(a["start"].strftime("%A"), cell_style),
                Paragraph(a["start"].strftime("%Y-%m-%d"), cell_style),
                Paragraph(f"{start_s}–{end_s}", cell_style),
                Paragraph(a.get("client_name") or "-", cell_style),
                Paragraph(a.get("mobile") or "-", cell_style),
                Paragraph(a.get("sub_service") or "-", cell_style),
                Paragraph(a.get("doctor") or "-", cell_style),
                Paragraph(a.get("duration") or "-", cell_style),
                Paragraph(a.get("channel") or "-", cell_style),
            ])

        # A4 landscape usable ~273mm. Tuned column widths (sum ≈ 273mm).
        col_widths = [10 * mm, 22 * mm, 25 * mm, 24 * mm, 38 * mm,
                      32 * mm, 40 * mm, 28 * mm, 22 * mm, 22 * mm]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6feb")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f4f6fa")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cfd6df")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(table)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def send_next_week_report():
    """Build per-department reports of next week's appointments and send via Telegram.

    Runs weekly (Thursday 18:00 in Oman) via APScheduler. Produces ONE text
    file per department and uploads each to the clinic's Telegram chat.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured; skipping weekly report")
        return

    now = datetime.now(_tz)
    week_start, week_end = _next_week_range(now)
    logger.info(f"Building weekly report for {week_start.date()} → {week_end.date()}")

    events = _fetch_events(week_start, week_end)
    cal_appts = _calendar_appointments(events)
    sheet_appts = _sheet_appointments(
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
    )
    grouped = _merge_and_group(cal_appts, sheet_appts)

    week_label = f"{week_start.strftime('%Y-%m-%d')}_to_{(week_end - timedelta(days=1)).strftime('%Y-%m-%d')}"

    header = (
        f"📅 Weekly Schedule — {config.BUSINESS_NAME}\n"
        f"Week: {week_start.strftime('%a %Y-%m-%d')} → {(week_end - timedelta(days=1)).strftime('%a %Y-%m-%d')}\n"
        f"Sending one file per department…"
    )
    try:
        telegram.send_message(header)
    except Exception as e:
        logger.error(f"Failed to send Telegram header message: {e}")

    for dept_key, label in DEPARTMENT_LABELS.items():
        appts = grouped.get(dept_key, [])
        pdf_bytes = _build_department_pdf(dept_key, appts, week_start, week_end)
        filename = f"{dept_key}_{week_label}.pdf"
        caption = f"{label} — {len(appts)} appointment(s)"
        try:
            telegram.send_document(
                filename, pdf_bytes, caption=caption, mime_type="application/pdf"
            )
            logger.info(f"Sent weekly PDF report for {dept_key}: {len(appts)} appointments")
        except Exception as e:
            logger.error(f"Failed to send Telegram PDF for {dept_key}: {e}")
