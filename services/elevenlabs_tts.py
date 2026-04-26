"""ElevenLabs text-to-speech for the voice agent.

Why a cache: a typical clinic call repeats the same opening line, the same
break-time clarification, the same goodbye, dozens of times a day. Each
re-generation costs characters from the ElevenLabs quota, which is the
biggest line item in the voice-agent budget. Caching the audio bytes
keyed on (text, voice, model, settings) cuts repeat costs by ~70%.

Cache lives on disk under conversations/tts_cache/ as MP3 files named by
SHA-1 of the cache key. Lookups are O(1) — Python opens the file if the
hash matches; otherwise we hit the API and write the file for next time.

Public API:
    synthesize(text, voice_id=None, ...) -> bytes  (MP3 audio)
    synthesize_to_file(text, out_path, ...) -> str (file path)
"""

import hashlib
import json
import logging
import os
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.elevenlabs.io/v1"
_CACHE_DIR = Path(__file__).resolve().parent.parent / "conversations" / "tts_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default voice settings — "natural warm" profile, dialed in based on
# live-call feedback. The goal is "real human Omani receptionist" energy:
# warm enough that callers feel welcomed, slow enough that nothing feels
# rushed, varied enough that callers don't think they're talking to a bot.
#
#   - stability 0.50  → enough natural variance to sound human, not chaotic
#   - similarity 0.85 → keep the cloned voice's identity intact
#   - style 0.30      → audible warmth without theatrical exaggeration
#   - speed 0.92      → relaxed pace, gives the caller time to follow
#
# Important: lower stability is what kills the "AI-sounding" feel. Above
# 0.65 the voice gets metronomic; below 0.40 it loses consistency.
DEFAULT_SETTINGS = {
    "stability": 0.50,
    "similarity_boost": 0.85,
    "style": 0.30,
    "speed": 0.92,
    "use_speaker_boost": True,
}


def _cache_key(text: str, voice_id: str, model: str, settings: dict) -> str:
    """SHA-1 hash of everything that affects the audio output."""
    payload = json.dumps(
        {"text": text, "voice": voice_id, "model": model, "settings": settings},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.mp3"


def synthesize(
    text: str,
    voice_id: str | None = None,
    model: str | None = None,
    settings: dict | None = None,
    use_cache: bool = True,
) -> bytes:
    """Render `text` to MP3 audio bytes using ElevenLabs.

    Raises RuntimeError on API errors or missing credentials. Empty or
    blank text raises ValueError before any network call.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("synthesize() requires non-empty text")

    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set in .env")

    voice_id = voice_id or config.ELEVENLABS_VOICE_ID
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is not set in .env")

    model = model or config.ELEVENLABS_MODEL
    settings = {**DEFAULT_SETTINGS, **(settings or {})}

    key = _cache_key(text, voice_id, model, settings)
    path = _cache_path(key)
    if use_cache and path.exists():
        logger.debug("TTS cache hit for key=%s (%d chars)", key[:8], len(text))
        return path.read_bytes()

    # Attach the clinic's pronunciation dictionary so brand names, OMR,
    # and Arabic medical terms are spoken correctly. Lazy import to avoid
    # circular dep at module load.
    from services import pronunciation
    locators = pronunciation.get_active_locators()

    logger.info(
        "TTS cache miss — calling ElevenLabs (voice=%s, %d chars, %d pron rules)",
        voice_id, len(text), sum(1 for _ in locators),
    )
    body: dict = {
        "text": text,
        "model_id": model,
        "voice_settings": settings,
    }
    if locators:
        body["pronunciation_dictionary_locators"] = locators

    response = requests.post(
        f"{_API_BASE}/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json=body,
        # optimize_streaming_latency=2 cuts generation time noticeably even
        # in non-streaming mode (it skips some smoothing passes). Level 2 is
        # the sweet spot for phone calls — quality drop is barely audible.
        params={"optimize_streaming_latency": 2},
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs API error {response.status_code}: {response.text[:300]}"
        )

    audio = response.content
    if use_cache:
        path.write_bytes(audio)
    return audio


def synthesize_to_file(
    text: str,
    out_path: str | Path,
    voice_id: str | None = None,
    model: str | None = None,
    settings: dict | None = None,
    use_cache: bool = True,
) -> str:
    """Convenience: render `text` and write the MP3 to `out_path`."""
    audio = synthesize(text, voice_id, model, settings, use_cache)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    return str(out_path)


def list_voices() -> list[dict]:
    """Return all voices the account has access to. Useful for picking a
    voice ID without leaving the codebase.
    """
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set in .env")
    response = requests.get(
        f"{_API_BASE}/voices",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("voices", [])


def get_quota() -> dict:
    """Return current character usage / limits for the account."""
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set in .env")
    response = requests.get(
        f"{_API_BASE}/user/subscription",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "tier": data.get("tier"),
        "character_count": data.get("character_count"),
        "character_limit": data.get("character_limit"),
        "remaining": (data.get("character_limit") or 0) - (data.get("character_count") or 0),
    }
