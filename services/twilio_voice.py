"""Twilio voice-webhook glue: build TwiML responses and pull caller
audio off Twilio's recording URLs.

Why we hand-write TwiML instead of using the twilio SDK: the responses
we emit are tiny (one or two verbs each) and depending on the SDK pulls
in a transitive dep tree we don't otherwise need. The XML is documented
at https://www.twilio.com/docs/voice/twiml — if we ever need PSTN
features beyond Play/Record/Hangup, swap to the SDK.

Recording flow:
  1. We send TwiML with <Record action="..." transcribe="false" />.
  2. The caller speaks, Twilio stops on silence, then POSTs to our
     `action` URL with `RecordingUrl` and `RecordingSid` form fields.
  3. We GET RecordingUrl + ".mp3" with HTTP basic auth (account_sid +
     auth_token) and feed the bytes into Deepgram.
"""

import logging
from xml.sax.saxutils import escape

import requests

import config

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """True only when every Twilio credential is populated."""
    return all([
        config.TWILIO_ACCOUNT_SID,
        config.TWILIO_AUTH_TOKEN,
        config.TWILIO_PUBLIC_BASE_URL,
    ])


def out_of_service_twiml() -> str:
    """Polite hangup when the voice agent isn't fully configured."""
    return _twiml(
        '<Say language="en-GB">'
        'Sorry, our voice service is temporarily unavailable. '
        'Please try again later or send us a WhatsApp message.'
        '</Say>'
        '<Hangup/>'
    )


def play_and_record_twiml(audio_url: str, action_url: str) -> str:
    """TwiML that plays our audio response, then records the next caller
    turn and posts the recording to action_url.

    `timeout=2` ends recording after 2 seconds of silence — short enough
    to keep the conversation snappy, long enough that the caller can
    pause for breath mid-thought without getting cut off.

    `maxLength=20` caps a single turn at 20 seconds so a stuck caller
    doesn't tie up the line forever.

    `playBeep=false` avoids the awkward voicemail-style beep before each
    turn — we want it to feel like a conversation, not a survey.
    """
    return _twiml(
        f'<Play>{escape(audio_url)}</Play>'
        f'<Record action="{escape(action_url)}" '
        f'method="POST" '
        f'timeout="3" '
        f'maxLength="20" '
        f'playBeep="false" '
        f'trim="trim-silence" '
        f'finishOnKey="" />'
    )


def play_and_hangup_twiml(audio_url: str) -> str:
    """Final goodbye — play audio then hang up."""
    return _twiml(
        f'<Play>{escape(audio_url)}</Play>'
        f'<Hangup/>'
    )


def _twiml(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def download_recording(recording_url: str) -> bytes:
    """Fetch the caller's recorded turn from Twilio in WAV format.

    Twilio's RecordingUrl in the webhook payload is the metadata URL —
    appending `.wav` gets the audio in uncompressed PCM (mu-law decoded).
    We use WAV instead of MP3 because every codec hop chips away at
    audio quality, and on phone-quality Arabic that quality cost shows
    up directly as STT misses. WAV is bigger but the whole call only
    moves a few hundred KB.
    """
    if not config.TWILIO_ACCOUNT_SID or not config.TWILIO_AUTH_TOKEN:
        raise RuntimeError("Twilio credentials are not configured")

    if not recording_url.endswith(".wav") and not recording_url.endswith(".mp3"):
        recording_url = recording_url + ".wav"

    response = requests.get(
        recording_url,
        auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Twilio recording fetch failed {response.status_code}: "
            f"{response.text[:200]}"
        )
    return response.content
