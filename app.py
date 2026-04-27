import base64
import logging
import os
import socket
from functools import wraps

# Cloud-deploy bootstrap: when GOOGLE_CREDENTIALS_JSON_B64 is set (Railway,
# Render, etc.) and credentials.json doesn't exist, decode the env var to
# disk so google_calendar / google_sheets can load it the normal way. This
# keeps the secret out of git while letting the same code run locally
# (where credentials.json sits next to the code) and in the cloud.
_creds_b64 = os.getenv("GOOGLE_CREDENTIALS_JSON_B64")
if _creds_b64 and not os.path.exists("credentials.json"):
    with open("credentials.json", "wb") as _f:
        _f.write(base64.b64decode(_creds_b64))

# Socket-level timeout for ALL outbound HTTP calls. Without this, a hung
# Google API SSL handshake can pin a Flask thread forever, eventually
# exhausting the worker pool and killing the server during phone calls.
socket.setdefaulttimeout(10)

from flask import Flask, request, jsonify, Response, render_template, redirect, url_for, session
from werkzeug.exceptions import HTTPException
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from services import whatsapp, instagram, speech_to_text, claude_ai
from services import twilio_voice, voice_audio_store, dashboard_data
from services.reminder import send_reminders
from services.sync import sync_all
from services.retention import run_retention
from services.packages_sheet import sync_packages
from services.voice_agent import VoiceSession

# Telegram integration is intentionally disabled. To re-enable later,
# import services.alerts + telegram_commands + weekly_report + token_monitor
# and re-add the scheduler jobs / webhook below.

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
# Telegram alert handler intentionally disabled — clinic-side noise was
# too high during dev and we don't want a future paying customer paged
# by a transient SSL hiccup. Re-enable by importing services.alerts and
# calling install_logging_handler() if you ever want it back.

app = Flask(__name__)
app.secret_key = config.DASHBOARD_SECRET_KEY

# In-memory dedup of Meta webhook message IDs. Meta retries failed deliveries
# (and sometimes also delivers the same message twice). Without dedup, the
# bot replies repeatedly or, worse, restarts the conversation hours later
# when an old retry lands. We keep the most recent N IDs and skip duplicates.
import collections
import time
_RECENT_MESSAGE_IDS: collections.OrderedDict[str, float] = collections.OrderedDict()
_DEDUP_MAX_AGE_SECONDS = 24 * 60 * 60   # remember IDs for 24h
_DEDUP_MAX_ENTRIES = 500
# Skip messages older than this — Meta retry loops can resurface old
# webhooks long after the conversation ended; treating them as fresh
# input creates phantom replies.
_STALE_MESSAGE_AGE_SECONDS = 5 * 60     # 5 minutes


def _is_duplicate_or_stale(message_id: str, timestamp: int) -> bool:
    """Return True if we've already processed this Meta message_id, or if
    its timestamp is too old to act on. Trims the dedup map opportunistically.
    """
    now = time.time()
    # Stale check
    if timestamp and (now - timestamp) > _STALE_MESSAGE_AGE_SECONDS:
        return True
    # Dedup check
    if not message_id:
        return False
    if message_id in _RECENT_MESSAGE_IDS:
        return True
    _RECENT_MESSAGE_IDS[message_id] = now
    # Trim by age first, then by size.
    cutoff = now - _DEDUP_MAX_AGE_SECONDS
    while _RECENT_MESSAGE_IDS:
        oldest_id = next(iter(_RECENT_MESSAGE_IDS))
        if _RECENT_MESSAGE_IDS[oldest_id] < cutoff:
            _RECENT_MESSAGE_IDS.popitem(last=False)
        else:
            break
    while len(_RECENT_MESSAGE_IDS) > _DEDUP_MAX_ENTRIES:
        _RECENT_MESSAGE_IDS.popitem(last=False)
    return False


@app.errorhandler(HTTPException)
def _http_error(e):
    """Werkzeug HTTP errors (404, 405, 410, etc.) are normal web traffic —
    bots scanning the public tunnel, callers refreshing stale URLs, Twilio
    occasionally probing. Log them at INFO so they show up in dev console
    but DO NOT send them to the Telegram alert channel.
    """
    logger.info("HTTP %s on %s: %s", e.code, request.path, e.description)
    return jsonify({"status": "error", "code": e.code}), e.code


@app.errorhandler(Exception)
def _unhandled(e):
    """Real, unexpected exceptions in our own code — these DO go to
    Telegram so the clinic knows something broke.
    """
    # Defensive: if a Werkzeug HTTPException slips past the handler above
    # (e.g., raised inside another handler), still don't spam Telegram.
    if isinstance(e, HTTPException):
        return _http_error(e)
    logger.error(f"Unhandled Flask error: {e}", exc_info=True)
    return jsonify({"status": "error"}), 500

# --- Scheduler ---
# Telegram-dependent jobs (weekly_report, token_monitor) are intentionally
# disabled — re-add them after re-enabling the alerts module.
scheduler = BackgroundScheduler(timezone=config.BUSINESS_TIMEZONE)
scheduler.add_job(send_reminders, "interval", hours=1)
# Bidirectional Sheet ↔ Calendar sync so manual entries on either side
# appear on the other within a couple of minutes.
scheduler.add_job(sync_all, "interval", minutes=2, id="sheet_calendar_sync")
# Daily retention scan at 10:00 Oman time: sends post-visit follow-ups
# (day 1/7/28/90/180/365) based on service type.
scheduler.add_job(
    run_retention,
    CronTrigger(hour=10, minute=0, timezone=config.BUSINESS_TIMEZONE),
    id="retention_campaigns",
)
# Packages sheet sync: receptionist adds rows to the "Packages" tab after
# receiving payment; every 3 minutes we create the package record and
# write back status/sessions-used.
scheduler.add_job(sync_packages, "interval", minutes=3, id="packages_sheet_sync")
scheduler.start()


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """WhatsApp webhook verification (GET request from Meta)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return challenge, 200

    logger.warning("Webhook verification failed.")
    return "Forbidden", 403


def _process_whatsapp_message(parsed: dict) -> None:
    """Heavy lifting (download voice, STT, Claude, send reply) — runs in
    a background thread so the webhook returns 200 to Meta immediately.

    Meta retries any webhook that doesn't 200 within ~5 seconds; voice
    transcription + Claude tool-use can easily take 10–30s, which used to
    cause Meta to retry mid-processing and produce duplicate/late replies.
    """
    from_phone = parsed["from_phone"]
    message_type = parsed["message_type"]

    if message_type == "audio" and parsed["media_id"]:
        logger.info(f"Voice message from {from_phone}, transcribing...")
        try:
            audio_bytes = whatsapp.download_media(parsed["media_id"])
            text = speech_to_text.transcribe(audio_bytes)
            logger.info(f"Transcribed: {text}")
        except Exception as e:
            logger.warning(f"Voice transcription failed: {e}")
            text = ""
        if not text.strip():
            whatsapp.send_message(
                from_phone,
                "آسفة، ما قدرت أسمع صوتك بوضوح. ممكن تكتبي رسالتك؟ 🙏\n\n"
                "Sorry, I couldn't hear your voice clearly. Could you type your message instead?",
            )
            return
    elif message_type == "text" and parsed["text"]:
        text = parsed["text"]
    else:
        return

    logger.info(f"Message from {from_phone}: {text}")
    try:
        reply = claude_ai.handle_message(from_phone, text, channel="whatsapp")
    except Exception as e:
        logger.error(f"Claude handle_message failed for {from_phone}: {e}", exc_info=True)
        return
    logger.info(f"Reply to {from_phone}: {reply}")

    if reply:
        whatsapp.send_message(from_phone, reply)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming WhatsApp messages.

    Returns 200 to Meta immediately, processes the actual reply in a
    background thread. Without this, transcribing a voice note + running
    Claude tools (5–30 seconds) makes Meta time out and retry, which
    previously caused duplicate replies and phantom welcomes.
    """
    import threading
    payload = request.get_json()
    logger.info(f"Received webhook payload")

    parsed = whatsapp.parse_incoming(payload)
    if not parsed:
        return jsonify({"status": "ignored"}), 200

    # Skip Meta retries and stale webhook deliveries.
    if _is_duplicate_or_stale(parsed.get("message_id", ""), parsed.get("timestamp", 0)):
        logger.info(
            "Skipping stale/duplicate WhatsApp message_id=%s ts=%s from=%s",
            parsed.get("message_id"), parsed.get("timestamp"), parsed.get("from_phone"),
        )
        return jsonify({"status": "skipped_duplicate_or_stale"}), 200

    # Hand off to a background thread so we 200 Meta immediately.
    threading.Thread(
        target=_process_whatsapp_message, args=(parsed,), daemon=True
    ).start()
    return jsonify({"status": "ok"}), 200


@app.route("/instagram/webhook", methods=["GET"])
def verify_instagram_webhook():
    """Instagram DM webhook verification (GET request from Meta)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == config.INSTAGRAM_VERIFY_TOKEN:
        logger.info("Instagram webhook verified successfully.")
        return challenge, 200

    logger.warning("Instagram webhook verification failed.")
    return "Forbidden", 403


def _process_instagram_message(parsed: dict) -> None:
    """Background-thread processor for an Instagram DM. Mirrors the
    WhatsApp version — runs after we've already 200'd Meta to avoid
    timeout-driven retries.
    """
    from_igsid = parsed["from_igsid"]
    message_type = parsed["message_type"]

    if message_type == "audio" and parsed["media_url"]:
        logger.info(f"Voice DM from {from_igsid}, transcribing...")
        try:
            audio_bytes = instagram.download_media(parsed["media_url"])
            text = speech_to_text.transcribe(audio_bytes)
            logger.info(f"Transcribed: {text}")
        except Exception as e:
            logger.warning(f"IG voice transcription failed: {e}")
            text = ""
        if not text.strip():
            instagram.send_message(
                from_igsid,
                "آسفة، ما قدرت أسمع صوتك بوضوح. ممكن تكتبي رسالتك؟ 🙏\n\n"
                "Sorry, I couldn't hear your voice clearly. Could you type your message instead?",
            )
            return
    elif message_type == "text" and parsed["text"]:
        text = parsed["text"]
    else:
        return

    logger.info(f"IG message from {from_igsid}: {text}")
    try:
        reply = claude_ai.handle_message(from_igsid, text, channel="instagram")
    except Exception as e:
        logger.error(f"Claude IG handle_message failed for {from_igsid}: {e}", exc_info=True)
        return
    logger.info(f"Reply to {from_igsid}: {reply}")
    if reply:
        instagram.send_message(from_igsid, reply)


@app.route("/instagram/webhook", methods=["POST"])
def handle_instagram_webhook():
    """Handle incoming Instagram DM messages — return 200 immediately,
    process in background thread (same reasoning as WhatsApp webhook).
    """
    import threading
    payload = request.get_json()
    logger.info("Received Instagram webhook payload")

    parsed = instagram.parse_incoming(payload)
    if not parsed:
        return jsonify({"status": "ignored"}), 200

    if _is_duplicate_or_stale(parsed.get("message_id", ""), parsed.get("timestamp", 0)):
        logger.info(
            "Skipping stale/duplicate IG mid=%s ts=%s from=%s",
            parsed.get("message_id"), parsed.get("timestamp"), parsed.get("from_igsid"),
        )
        return jsonify({"status": "skipped_duplicate_or_stale"}), 200

    threading.Thread(
        target=_process_instagram_message, args=(parsed,), daemon=True
    ).start()
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "running"}), 200


# NOTE: /admin/reset_conversation lives lower in the file, after the
# _admin_required decorator is defined. Keep this comment as a pointer.


# ============================================================================
# Twilio voice agent — incoming phone calls
# ============================================================================
#
# Conversation lifecycle:
#   /voice/incoming     ← Twilio POSTs when the phone rings
#       returns TwiML: play greeting + record caller's first turn
#   /voice/respond      ← Twilio POSTs after each caller turn (with audio URL)
#       returns TwiML: play bot reply + record next caller turn
#   /voice/audio/<id>   ← Twilio GETs to fetch the MP3 we generated
#
# We keep the per-call Claude history under conversations/voice/, keyed by
# the caller's phone number. So if the call drops and they call back, the
# context resumes naturally.

def _audio_url(file_id: str) -> str:
    """Public URL Twilio will GET to fetch one of our generated MP3s."""
    base = config.TWILIO_PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/voice/audio/{file_id}"


def _respond_url() -> str:
    base = config.TWILIO_PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/voice/respond"


@app.route("/voice/incoming", methods=["POST"])
def voice_incoming():
    """Twilio webhook for the start of a call. Plays the greeting and starts
    recording the caller's first turn.
    """
    if not twilio_voice.is_configured():
        logger.error("Twilio webhook hit but credentials are not set")
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    caller = request.form.get("From", "unknown")
    logger.info("Incoming voice call from %s", caller)

    try:
        session = VoiceSession(caller_phone=caller)
        greeting_audio = session.greeting()
        file_id = voice_audio_store.store(greeting_audio)
    except Exception as e:
        logger.error("Greeting generation failed for %s: %s", caller, e, exc_info=True)
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    twiml = twilio_voice.play_and_record_twiml(
        audio_url=_audio_url(file_id),
        action_url=_respond_url(),
    )
    return Response(twiml, mimetype="application/xml")


@app.route("/voice/respond", methods=["POST"])
def voice_respond():
    """Twilio webhook called after each caller turn — they spoke, Twilio
    recorded it, now Twilio sends us the recording URL so we can transcribe
    and reply.
    """
    if not twilio_voice.is_configured():
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    caller = request.form.get("From", "unknown")
    recording_url = request.form.get("RecordingUrl", "")

    if not recording_url:
        # Caller likely hung up without speaking — wrap up.
        logger.info("No recording URL from %s, ending call", caller)
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    try:
        audio_in = twilio_voice.download_recording(recording_url)
        session = VoiceSession(caller_phone=caller)
        # Twilio recordings are downloaded as WAV (uncompressed) for best
        # STT accuracy on phone-quality Arabic.
        result = session.respond_to_audio(audio_in, audio_mime="audio/wav")
    except Exception as e:
        logger.error("Voice turn failed for %s: %s", caller, e, exc_info=True)
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    logger.info("Caller=%s heard=%r reply=%r",
                caller, result.get("transcript"), result.get("reply_text"))

    if not result.get("audio"):
        # Claude returned no audio (rare); hang up gracefully.
        return Response(twilio_voice.out_of_service_twiml(), mimetype="application/xml")

    file_id = voice_audio_store.store(result["audio"])
    twiml = twilio_voice.play_and_record_twiml(
        audio_url=_audio_url(file_id),
        action_url=_respond_url(),
    )
    return Response(twiml, mimetype="application/xml")


@app.route("/voice/audio/<file_id>", methods=["GET"])
def voice_audio(file_id: str):
    """Serve a previously-stored MP3 to Twilio's <Play> verb."""
    audio = voice_audio_store.retrieve(file_id)
    if audio is None:
        return Response("not found", status=404)
    return Response(audio, mimetype="audio/mpeg")


# ============================================================================
# Admin dashboard — read-only operational view for the clinic owner.
# ============================================================================
#
# Routes:
#   /admin/login        password gate (POST submits)
#   /admin/logout       clears session
#   /admin/dashboard    main view (HTMX shell — partials below feed widgets)
#   /admin/_kpis etc.   HTMX partial endpoints, refreshed on intervals
#
# Auth is a single shared password from config.DASHBOARD_PASSWORD held in a
# Flask session cookie. Good enough for a single-clinic deployment behind a
# tunnel; swap for proper auth before exposing to the open internet.

def _admin_required(view_fn):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login"))
        return view_fn(*args, **kwargs)
    return wrapped


@app.route("/admin", methods=["GET"])
def admin_root():
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.DASHBOARD_PASSWORD:
            session["admin_authed"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin/login.html", error="Wrong password.")
    if session.get("admin_authed"):
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/login.html")


@app.route("/admin/logout", methods=["GET"])
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard", methods=["GET"])
@_admin_required
def admin_dashboard():
    # Initial render fills with data so the page isn't empty before HTMX
    # fires — avoids a flash of empty widgets on first paint.
    return render_template(
        "admin/dashboard.html",
        kpis=dashboard_data.kpis(),
        view="today",
        days=dashboard_data.appointments_grouped_by_day(1),
        week_chart=dashboard_data.appointments_per_day(7),
        conversations=dashboard_data.recent_conversations(10),
        departments=dashboard_data.department_breakdown_today(),
        pkgs=dashboard_data.active_packages_summary(),
        revenue_today=dashboard_data.revenue_today(),
        revenue_week=dashboard_data.revenue_this_week(),
        waitlist=dashboard_data.waitlist_summary(),
    )


# --- HTMX partial endpoints (one per widget, refreshed independently) ---

@app.route("/admin/_kpis", methods=["GET"])
@_admin_required
def admin_partial_kpis():
    return render_template("admin/_kpis.html", kpis=dashboard_data.kpis())


@app.route("/admin/_today", methods=["GET"])
@_admin_required
def admin_partial_today():
    return render_template("admin/_today.html", today=dashboard_data.appointments_today())


@app.route("/admin/_appointments", methods=["GET"])
@_admin_required
def admin_partial_appointments():
    """Tabbed appointments view. ?view=today|week|month."""
    view = request.args.get("view", "today")
    days_map = {"today": 1, "week": 7, "month": 30}
    days = dashboard_data.appointments_grouped_by_day(days_map.get(view, 1))
    return render_template("admin/_appointments.html", view=view, days=days)


@app.route("/admin/_week", methods=["GET"])
@_admin_required
def admin_partial_week():
    return render_template("admin/_week.html", week_chart=dashboard_data.appointments_per_day(7))


@app.route("/admin/_conversations", methods=["GET"])
@_admin_required
def admin_partial_conversations():
    return render_template("admin/_conversations.html", conversations=dashboard_data.recent_conversations(10))


@app.route("/admin/_departments", methods=["GET"])
@_admin_required
def admin_partial_departments():
    return render_template("admin/_departments.html", departments=dashboard_data.department_breakdown_today())


@app.route("/admin/_packages", methods=["GET"])
@_admin_required
def admin_partial_packages():
    return render_template("admin/_packages.html", pkgs=dashboard_data.active_packages_summary())


@app.route("/admin/_revenue", methods=["GET"])
@_admin_required
def admin_partial_revenue():
    return render_template(
        "admin/_revenue.html",
        revenue_today=dashboard_data.revenue_today(),
        revenue_week=dashboard_data.revenue_this_week(),
    )


@app.route("/admin/reset_conversation/<user_id>", methods=["POST", "GET"])
@_admin_required
def admin_reset_conversation(user_id: str):
    """Wipe a single client's conversation history across all channels.

    Useful when a previous session went off the rails (e.g. wrong language,
    test data leaked into history) and you want the bot to start fresh
    with that client. Login required.
    """
    from pathlib import Path
    # Match the same id-to-filename transform claude_ai._conv_path uses.
    safe = "".join(c for c in user_id if c.isalnum())
    base = Path(__file__).parent / "conversations"
    deleted = []
    for sub in (None, "instagram", "voice"):
        path = (base / sub / f"{safe}.json") if sub else (base / f"{safe}.json")
        if path.exists():
            path.unlink()
            deleted.append(str(path.relative_to(base)))
    return jsonify({"deleted": deleted, "user_id": user_id, "safe_id": safe}), 200


@app.route("/admin/_waitlist", methods=["GET"])
@_admin_required
def admin_partial_waitlist():
    return render_template("admin/_waitlist.html", waitlist=dashboard_data.waitlist_summary())


if __name__ == "__main__":
    import os
    logger.info(f"Starting Receptionist for {config.BUSINESS_NAME} (WhatsApp + Instagram)")
    # Serve via waitress (production WSGI) instead of Flask's dev server.
    # Flask dev server proved unstable under concurrent webhook + dashboard
    # load — it would silently die during live phone calls. Waitress is
    # pure-Python, multi-threaded, and survives long-running connections.
    # PORT env var lets Railway / Render / other PaaS pick the listen port;
    # falls back to 5000 for local dev.
    port = int(os.getenv("PORT", "5000"))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=8, channel_timeout=60)
