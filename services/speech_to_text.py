"""Speech-to-text for WhatsApp / Instagram voice messages.

Uses Deepgram's whisper-large model (Arabic-forced) — same provider we use
for the live phone-call agent, so we share one API key and one $200 free
credit pool. OpenAI Whisper was the previous backend but its account
billing went inactive; rather than juggle two STT providers, we
consolidated on Deepgram.

Returns empty string on any transcription failure so the caller can
gracefully ask the user to send text instead.
"""

import logging
import tempfile
from pathlib import Path

from services import deepgram_stt

logger = logging.getLogger(__name__)


def transcribe(audio_bytes: bytes, mime: str = "audio/ogg") -> str:
    """Transcribe audio bytes to text. WhatsApp sends OGG/Opus, IG sends mp4."""
    if not audio_bytes:
        return ""

    suffix = ".ogg"
    if "mp4" in mime or "m4a" in mime:
        suffix = ".m4a"
    elif "mpeg" in mime or "mp3" in mime:
        suffix = ".mp3"
    elif "wav" in mime:
        suffix = ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    # For WhatsApp/IG voice messages, auto-detect language. Phone-call STT
    # forces language=ar because Omani callers will always speak Arabic, but
    # WhatsApp callers might write English voice notes — we want the bot to
    # reply in whichever language they actually used.
    try:
        result = deepgram_stt.transcribe_file(
            tmp_path,
            params={
                "model": "whisper-large",
                "detect_language": "true",
                "smart_format": "true",
                "punctuate": "true",
            },
        )
        return deepgram_stt.extract_transcript(result) or ""
    except Exception as e:
        logger.warning("Whisper/Deepgram transcription failed: %s", e)
        return ""
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
