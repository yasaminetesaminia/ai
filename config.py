import os
from dotenv import load_dotenv

load_dotenv()

# WhatsApp
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

# Instagram (Graph API — Messenger platform for Instagram DMs)
INSTAGRAM_TOKEN = os.getenv("INSTAGRAM_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")
INSTAGRAM_VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN")

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# OpenAI (Whisper)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Google
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")

# Telegram (weekly schedule report to clinic)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Meta App credentials (used by the token-expiry monitor). If either is
# missing, the monitor is skipped silently.
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")

# ElevenLabs (TTS for voice agent — used to synthesize bot replies during
# phone calls). The voice ID is swappable via .env so the clinic can move
# from a stock voice to a cloned voice without any code change.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Deepgram (STT — streaming speech-to-text for voice agent phone calls).
# Used for real-time transcription during live phone conversations.
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Business
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Noora Clinic")
BUSINESS_WORKING_HOURS_START = os.getenv("BUSINESS_WORKING_HOURS_START", "10:00")
BUSINESS_WORKING_HOURS_END = os.getenv("BUSINESS_WORKING_HOURS_END", "20:00")
BUSINESS_LASER_END = os.getenv("BUSINESS_LASER_END", "23:00")
BUSINESS_BREAK_START = os.getenv("BUSINESS_BREAK_START", "14:00")
BUSINESS_BREAK_END = os.getenv("BUSINESS_BREAK_END", "15:00")
BUSINESS_TIMEZONE = os.getenv("BUSINESS_TIMEZONE", "Asia/Dubai")
BUSINESS_CLOSED_DAY = os.getenv("BUSINESS_CLOSED_DAY", "Friday")

# Public holidays (comma-separated YYYY-MM-DD). Clinic is fully closed on these dates.
_holidays_raw = os.getenv("BUSINESS_HOLIDAYS", "")
BUSINESS_HOLIDAYS = [d.strip() for d in _holidays_raw.split(",") if d.strip()]

# Clinic location & contact — surfaced to the voice agent for callers
# asking where we're located, how to reach us after hours, etc.
CLINIC_ADDRESS_EN = os.getenv("CLINIC_ADDRESS_EN", "Muscat, Oman")
CLINIC_ADDRESS_AR = os.getenv("CLINIC_ADDRESS_AR", "مسقط، سلطنة عمان")
CLINIC_EMERGENCY_PHONE = os.getenv("CLINIC_EMERGENCY_PHONE", "")
CLINIC_INSTAGRAM = os.getenv("CLINIC_INSTAGRAM", "")
CLINIC_PARKING = os.getenv("CLINIC_PARKING", "available").lower() == "available"

# Twilio (telephony) — set once you have an account. The voice webhooks
# detect missing credentials and return an "out of service" TwiML so a
# misconfigured deploy doesn't drop calls silently.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_PUBLIC_BASE_URL = os.getenv("TWILIO_PUBLIC_BASE_URL", "")

# Admin dashboard auth (basic password gate; not strong but enough for
# a single-clinic deployment behind a tunnel).
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "noora2026")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "dev-only-change-me")
