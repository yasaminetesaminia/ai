import logging
import socket
from functools import wraps

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


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming WhatsApp messages."""
    payload = request.get_json()
    logger.info(f"Received webhook payload")

    parsed = whatsapp.parse_incoming(payload)
    if not parsed:
        return jsonify({"status": "ignored"}), 200

    from_phone = parsed["from_phone"]
    message_type = parsed["message_type"]

    # Convert voice to text if needed
    if message_type == "audio" and parsed["media_id"]:
        logger.info(f"Voice message from {from_phone}, transcribing...")
        audio_bytes = whatsapp.download_media(parsed["media_id"])
        text = speech_to_text.transcribe(audio_bytes)
        logger.info(f"Transcribed: {text}")
    elif message_type == "text" and parsed["text"]:
        text = parsed["text"]
    else:
        return jsonify({"status": "unsupported_type"}), 200

    # Process with Claude AI
    logger.info(f"Message from {from_phone}: {text}")
    reply = claude_ai.handle_message(from_phone, text, channel="whatsapp")
    logger.info(f"Reply to {from_phone}: {reply}")

    # Send response via WhatsApp
    whatsapp.send_message(from_phone, reply)

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


@app.route("/instagram/webhook", methods=["POST"])
def handle_instagram_webhook():
    """Handle incoming Instagram DM messages."""
    payload = request.get_json()
    logger.info("Received Instagram webhook payload")

    parsed = instagram.parse_incoming(payload)
    if not parsed:
        return jsonify({"status": "ignored"}), 200

    from_igsid = parsed["from_igsid"]
    message_type = parsed["message_type"]

    # Convert voice to text if needed
    if message_type == "audio" and parsed["media_url"]:
        logger.info(f"Voice DM from {from_igsid}, transcribing...")
        audio_bytes = instagram.download_media(parsed["media_url"])
        text = speech_to_text.transcribe(audio_bytes)
        logger.info(f"Transcribed: {text}")
    elif message_type == "text" and parsed["text"]:
        text = parsed["text"]
    else:
        return jsonify({"status": "unsupported_type"}), 200

    # Process with Claude AI (Instagram channel → IG prompt + IG conversation store)
    logger.info(f"IG message from {from_igsid}: {text}")
    reply = claude_ai.handle_message(from_igsid, text, channel="instagram")
    logger.info(f"Reply to {from_igsid}: {reply}")

    # Send response via Instagram DM
    instagram.send_message(from_igsid, reply)

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "running"}), 200


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
