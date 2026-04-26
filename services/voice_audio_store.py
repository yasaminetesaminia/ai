"""Short-lived store for generated audio responses, served back to Twilio
during a live phone call.

Why this exists: Twilio's `<Play>` verb takes a URL — it can't take raw
audio bytes. So after we generate a TTS response we have to (1) save it
where the public webhook URL can reach it and (2) hand Twilio a URL it
can fetch within the call.

Files live under conversations/voice_serve/ and are auto-deleted after
TTL_MINUTES on every store() call. That keeps the directory from
growing without bound and ensures call audio never lingers on disk.
"""

import hashlib
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent.parent / "conversations" / "voice_serve"
_DIR.mkdir(parents=True, exist_ok=True)

TTL_MINUTES = 30
_lock = threading.Lock()


def _cleanup_old() -> None:
    """Delete audio files older than TTL_MINUTES. Best-effort, non-fatal."""
    cutoff = time.time() - (TTL_MINUTES * 60)
    for path in _DIR.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def store(audio_bytes: bytes) -> str:
    """Persist audio_bytes and return a short opaque file_id.

    The id is the SHA-1 prefix of the bytes — same audio dedupes for free,
    and the id is short enough to fit comfortably in a TwiML <Play> URL.
    """
    file_id = hashlib.sha1(audio_bytes).hexdigest()[:16]
    path = _DIR / f"{file_id}.mp3"
    with _lock:
        _cleanup_old()
        if not path.exists():
            path.write_bytes(audio_bytes)
    return file_id


def retrieve(file_id: str) -> bytes | None:
    """Return audio bytes for file_id, or None if missing/expired."""
    if not _is_safe_id(file_id):
        return None
    path = _DIR / f"{file_id}.mp3"
    if not path.exists():
        return None
    return path.read_bytes()


def _is_safe_id(file_id: str) -> bool:
    """Reject anything that isn't a hex string we ourselves generated.
    Prevents path traversal via crafted file_id from a Twilio impersonator.
    """
    if not file_id or len(file_id) > 64:
        return False
    return all(c in "0123456789abcdef" for c in file_id.lower())
