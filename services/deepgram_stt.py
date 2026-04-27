"""Deepgram speech-to-text for the voice agent.

Two modes:

1. **Prerecorded transcription** (`transcribe_file`): Upload an audio file
   and get the transcript back. Good for testing the pipeline end-to-end
   with sample MP3s before phone calls are wired up.

2. **Streaming transcription** (`StreamingClient`): Open a WebSocket,
   push audio chunks as they arrive from Twilio Media Streams, and get
   interim + final transcripts in real time. This is what powers the
   live phone conversation — with ~300ms latency, the caller barely
   notices any pause.

Model selection: `nova-3-general` for the best quality on Arabic +
English. Multilingual detection means the caller can code-switch between
Omani Arabic and English mid-sentence and we still catch it correctly.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Callable

import requests

import config

logger = logging.getLogger(__name__)

_REST_BASE = "https://api.deepgram.com/v1"
_WS_BASE = "wss://api.deepgram.com/v1/listen"

# Default tuned for live phone calls in an Omani clinic — Arabic-first.
#
# Real-call testing surfaced two failure modes with the previous defaults
# (whisper-medium + detect_language=true):
#   1. Phone audio is far lower quality than ElevenLabs MP3s; whisper
#      sometimes detected Arabic speech as English, causing Claude to
#      reply in English to an Arabic caller.
#   2. On silent / noisy / very short clips, whisper hallucinates well-
#      known training-set phrases like "Thanks for watching!" — these
#      then get fed to Claude as if the caller said them.
#
# Forcing language=ar fixes both: whisper produces Arabic transcripts
# (English mid-Arabic words still come through), and the language-mismatch
# branch that triggers "Thanks for watching" hallucinations stays cold.
# whisper-large is the most accurate on phone-quality audio — the extra
# ~700ms is worth it given the alternative is a wrong transcript.
DEFAULT_PARAMS = {
    # whisper-large is ~700ms slower than -medium but noticeably more
    # accurate on phone-quality Arabic. Until we have a local Oman SIP
    # trunk delivering higher-quality audio, every bit of accuracy matters.
    "model": "whisper-large",
    "language": "ar",
    "smart_format": "true",  # numbers, punctuation
    "punctuate": "true",
    "diarize": "false",       # only the caller is on our side of the line
}

# Phrases Whisper is known to emit as hallucinations when the audio is
# silent, very noisy, or below its confidence floor. Treat any transcript
# matching one of these as "no input" so the bot asks the caller to repeat
# instead of dispatching Claude on garbage. Match is case-insensitive and
# trimmed; substrings count so trailing punctuation doesn't matter.
WHISPER_HALLUCINATION_PHRASES = [
    # English YouTube-training noise
    "thanks for watching",
    "thank you for watching",
    "subtitles by",
    "subscribe to my channel",
    "see you in the next video",
    "i'll see you next time",
    "translation by",
    "please subscribe",
    "transcribed by",
    "amara.org",
    "okay, but hi",
    # Arabic YouTube-training noise (very common on phone-quality audio)
    "اشتركوا في القناة",
    "اشتركوا فالقناة",
    "اشترك في القناة",
    "اشتركوا بالقناة",
    "ترجمة نانسي قنقر",
    "شكرا على المشاهدة",
    "شكرا للمشاهدة",
    "شكراً لمتابعتكم",
    "لا تنسوا الاشتراك",
    "اشتركو لايك",
    "ترجمة",
    # Single-character / punctuation-only output = silence
    ".",
    "،",
    "؟",
]


def is_likely_hallucination(text: str) -> bool:
    """True if the transcript matches a known whisper silence/noise hallucination."""
    if not text:
        return True
    t = text.strip().lower()
    if not t or len(t) < 2:
        return True
    return any(p in t for p in WHISPER_HALLUCINATION_PHRASES)


def transcribe_file(
    audio_path: str | Path,
    params: dict | None = None,
) -> dict:
    """Transcribe a local audio file (mp3/wav/m4a/ogg) and return the result.

    Returns a dict with the full Deepgram response. The transcript is at
    result["results"]["channels"][0]["alternatives"][0]["transcript"].

    Raises RuntimeError on API errors or missing credentials.
    """
    if not config.DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is not set in .env")

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"No audio file at {audio_path}")

    merged = {**DEFAULT_PARAMS, **(params or {})}

    mime = _guess_mime(audio_path)
    logger.info(
        "Deepgram transcribe: file=%s (%.1f KB) params=%s",
        audio_path.name, audio_path.stat().st_size / 1024, merged,
    )

    with audio_path.open("rb") as f:
        response = requests.post(
            f"{_REST_BASE}/listen",
            headers={
                "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                "Content-Type": mime,
            },
            params=merged,
            data=f.read(),
            timeout=120,
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Deepgram API error {response.status_code}: {response.text[:400]}"
        )
    return response.json()


def extract_transcript(result: dict) -> str:
    """Pull the first-channel transcript out of a Deepgram response."""
    try:
        alts = result["results"]["channels"][0]["alternatives"]
        return alts[0]["transcript"] if alts else ""
    except (KeyError, IndexError, TypeError):
        return ""


def extract_confidence(result: dict) -> float:
    """Overall confidence (0..1) of the top transcript. 0 if missing."""
    try:
        alts = result["results"]["channels"][0]["alternatives"]
        return float(alts[0].get("confidence", 0.0)) if alts else 0.0
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0


def extract_detected_language(result: dict) -> str | None:
    """Language code Deepgram detected (e.g. 'ar', 'en'), or None."""
    try:
        return result["results"]["channels"][0]["detected_language"]
    except (KeyError, IndexError, TypeError):
        return None


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(suffix, "audio/mpeg")


class StreamingClient:
    """WebSocket client for live transcription during phone calls.

    Usage (called from the Twilio Media Streams handler):

        client = StreamingClient(on_transcript=handle_text)
        client.start(encoding="mulaw", sample_rate=8000)
        for audio_chunk in twilio_stream:
            client.send_audio(audio_chunk)
        client.finish()

    `on_transcript` receives (text, is_final) — use is_final=True events
    to trigger Claude; interim ones are for UI feedback only.

    We import `websocket` lazily so `pip install websocket-client` is
    only needed when the voice agent is actually used (not during test
    runs that only touch the prerecorded path).
    """

    def __init__(self, on_transcript: Callable[[str, bool], None]):
        self._on_transcript = on_transcript
        self._ws = None
        self._thread = None
        self._closed = False

    def start(
        self,
        encoding: str = "mulaw",
        sample_rate: int = 8000,
        language: str = "multi",
    ) -> None:
        """Open the WebSocket and begin receiving transcripts."""
        try:
            import websocket  # type: ignore[import]
        except ImportError as e:
            raise RuntimeError(
                "streaming requires the 'websocket-client' package "
                "(add to requirements.txt)"
            ) from e

        if not config.DEEPGRAM_API_KEY:
            raise RuntimeError("DEEPGRAM_API_KEY is not set in .env")

        # Twilio phone audio is mulaw 8kHz. For other sources adjust.
        params = {
            "model": "nova-3",
            "language": language,
            "encoding": encoding,
            "sample_rate": str(sample_rate),
            "channels": "1",
            "smart_format": "true",
            "punctuate": "true",
            "interim_results": "true",
            "endpointing": "300",  # ms of silence before finalizing
            "utterance_end_ms": "1000",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{_WS_BASE}?{query}"

        self._ws = websocket.WebSocketApp(
            url,
            header={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=lambda *_: logger.info("Deepgram WS closed"),
        )
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def _on_message(self, _ws, message: str) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return
        channel = data.get("channel") or {}
        alts = channel.get("alternatives") or []
        if not alts:
            return
        text = (alts[0].get("transcript") or "").strip()
        if not text:
            return
        is_final = bool(data.get("is_final"))
        try:
            self._on_transcript(text, is_final)
        except Exception as e:
            logger.error("on_transcript callback failed: %s", e)

    def _on_error(self, _ws, error) -> None:
        logger.error("Deepgram WS error: %s", error)

    def send_audio(self, audio_bytes: bytes) -> None:
        """Push one chunk of raw audio to Deepgram."""
        if self._ws and not self._closed:
            self._ws.send(audio_bytes, opcode=0x2)  # binary frame

    def finish(self) -> None:
        """Flush and close the WebSocket."""
        if self._ws and not self._closed:
            try:
                self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            self._ws.close()
            self._closed = True


def get_project_balance() -> dict:
    """Return remaining credit (for observability / cost monitoring)."""
    if not config.DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is not set in .env")
    # /projects returns the list of projects this key can see.
    response = requests.get(
        f"{_REST_BASE}/projects",
        headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
        timeout=15,
    )
    response.raise_for_status()
    projects = response.json().get("projects", [])
    if not projects:
        return {"projects": 0}
    pid = projects[0]["project_id"]
    balance = requests.get(
        f"{_REST_BASE}/projects/{pid}/balances",
        headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
        timeout=15,
    )
    balance.raise_for_status()
    return balance.json()
