"""Phone-call voice agent: STT → Claude → TTS orchestration.

One `VoiceSession` per call. Each session tracks:
  - caller phone number (same role as client_phone in chat bots)
  - conversation history (Claude messages)
  - language once the caller chose one

The session's two entry points:

    session = VoiceSession(caller_phone="+968...")
    reply_audio = session.greeting()                      # first turn
    reply_audio = session.respond_to_audio(audio_bytes)   # each subsequent turn

Both return MP3 bytes. For testing we save them to disk; for a real
phone call, the Twilio layer will forward them back through Media
Streams. Keeping this layer transport-agnostic makes it easy to wire up
Twilio later without rewriting the logic.

Conversation history is persisted under conversations/voice/<phone>.json
so if a caller hangs up and calls back within the session, we pick up
where we left off.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

import config
from prompts.voice_receptionist import SYSTEM_PROMPT as VOICE_PROMPT, TOOLS
from services import deepgram_stt, elevenlabs_tts
# Reuse the existing Claude tool dispatcher so phone calls hit the same
# booking/packages/waitlist logic as WhatsApp and Instagram.
from services.claude_ai import _execute_tool

logger = logging.getLogger(__name__)

_CONV_DIR = Path(__file__).resolve().parent.parent / "conversations" / "voice"
_CONV_DIR.mkdir(parents=True, exist_ok=True)

MAX_HISTORY = 20  # phone calls are short; lower history = faster Claude
MAX_CLAUDE_ITERS = 6  # hard stop on tool-call loops

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _safe_filename(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "+-_") or "unknown"


class VoiceSession:
    """One phone call's worth of state + audio I/O helpers."""

    def __init__(self, caller_phone: str):
        self.caller_phone = caller_phone
        self._history_path = _CONV_DIR / f"{_safe_filename(caller_phone)}.json"
        self.history: list[dict] = self._load_history()

    # ---------- persistence ----------
    def _load_history(self) -> list[dict]:
        if not self._history_path.exists():
            return []
        try:
            return json.loads(self._history_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_history(self) -> None:
        clean = [
            {"role": m["role"], "content": _normalize_content(m["content"])}
            for m in self.history
        ]
        self._history_path.write_text(
            json.dumps(clean, ensure_ascii=False), encoding="utf-8"
        )

    # ---------- public audio API ----------
    def greeting(self) -> bytes:
        """Short bilingual opening — Arabic first (Omani-style "أهلاً فيك")
        so Arabic-speaking callers feel at home, then English for everyone
        else. The whole thing fits in a single breath (~3 seconds) — long
        greetings make the line feel slow before the conversation starts.

        Cached after first generation so repeat callers get sub-100ms playback.
        """
        text = "أهلاً فيك في عيادة لافورا. Welcome to Lavora Clinic."
        return elevenlabs_tts.synthesize(text)

    def respond_to_audio(self, audio_bytes: bytes, audio_mime: str = "audio/mpeg") -> dict:
        """One caller turn: transcribe, let Claude reply, synthesize.

        Returns {"transcript": ..., "reply_text": ..., "audio": <bytes>}.
        """
        transcript = _transcribe_bytes(audio_bytes, audio_mime)
        return self.respond_to_text(transcript)

    def respond_to_text(self, caller_text: str) -> dict:
        """Same as respond_to_audio but when the input is already text
        (useful for tests where we skip STT and feed Claude directly).
        """
        caller_text = (caller_text or "").strip()
        # Filter Whisper hallucinations ("Thanks for watching!" on silence).
        # Treat them as silence so we re-prompt instead of dispatching Claude
        # on noise — this avoids the bot replying to phantom phrases.
        if not caller_text or deepgram_stt.is_likely_hallucination(caller_text):
            # Cached short Arabic re-prompt: faster + more natural than English.
            reply = "آسفة، ما سمعت. ممكن تعيدي؟"
            return {
                "transcript": caller_text,
                "reply_text": reply,
                "audio": elevenlabs_tts.synthesize(reply),
            }

        # If transcript contains Persian script, wrap with hard "reply in
        # Arabic" instruction so Claude doesn't drift into a Persian reply.
        from services.claude_ai import _looks_persian, _wrap_for_persian
        msg_for_claude = (
            _wrap_for_persian(caller_text)
            if _looks_persian(caller_text) else caller_text
        )
        self.history.append({"role": "user", "content": msg_for_claude})
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

        reply_text = self._run_claude()

        self._save_history()
        audio = elevenlabs_tts.synthesize(reply_text) if reply_text else b""

        return {
            "transcript": caller_text,
            "reply_text": reply_text,
            "audio": audio,
        }

    # ---------- Claude loop ----------
    def _run_claude(self) -> str:
        """Drive Claude through tool-use iterations and return the final text."""
        system = self._build_system()

        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,  # shorter replies on the phone — speed > completeness
            system=system,
            tools=TOOLS,
            messages=self.history,
        )

        iters = 0
        while any(getattr(b, "type", None) == "tool_use" for b in response.content):
            if iters >= MAX_CLAUDE_ITERS:
                logger.warning("Voice agent hit tool-loop cap for %s", self.caller_phone)
                break
            iters += 1

            assistant_content = response.content
            self.history.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                # Channel="whatsapp" so reminders still go via WhatsApp to
                # the caller's phone (same number, same real human).
                try:
                    result = _execute_tool(block.name, block.input, channel="whatsapp")
                except Exception as e:
                    # If a tool raises (e.g. Google API blip), don't crash the
                    # whole turn — feed the error back to Claude so it can
                    # apologise/retry verbally instead of silently dropping.
                    logger.error(
                        "Tool %s failed for %s: %s",
                        block.name, self.caller_phone, e, exc_info=True,
                    )
                    result = json.dumps({
                        "success": False,
                        "error": f"{block.name} failed: {e}",
                        "message": "Tool error — apologise to the caller and offer to try again.",
                    })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            self.history.append({"role": "user", "content": tool_results})

            response = _client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=system,
                tools=TOOLS,
                messages=self.history,
            )

        assistant_content = response.content
        self.history.append({"role": "assistant", "content": assistant_content})

        return "".join(
            getattr(b, "text", "") for b in assistant_content
            if hasattr(b, "text")
        ).strip()

    def _build_system(self) -> list[dict]:
        """System blocks with prompt caching + current-date context."""
        now = datetime.now(ZoneInfo(config.BUSINESS_TIMEZONE))
        today_str = now.strftime("%Y-%m-%d")
        day_name = now.strftime("%A")
        time_str = now.strftime("%H:%M")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        is_friday = day_name.lower() == config.BUSINESS_CLOSED_DAY.lower()
        is_holiday = today_str in config.BUSINESS_HOLIDAYS
        upcoming_holidays = [h for h in config.BUSINESS_HOLIDAYS if h >= today_str][:5]

        status = ""
        if is_friday:
            status = "Clinic is CLOSED today (Friday)."
        elif is_holiday:
            status = "Clinic is CLOSED today (public holiday)."

        context = (
            f"\n## Current Context (authoritative)\n"
            f"- Today: {today_str} ({day_name})\n"
            f"- Current time: {time_str} ({config.BUSINESS_TIMEZONE})\n"
            f"- Tomorrow: {tomorrow_str}\n"
            f"- Upcoming closed dates: {', '.join(upcoming_holidays) or 'none'}\n"
            f"{status}\n"
            f"\n## Current Caller\n"
            f"Caller Phone (use as `client_phone` AND `client_mobile`): {self.caller_phone}"
        )

        return [
            {"type": "text", "text": VOICE_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": context},
        ]


# ---------- helpers ----------
def _normalize_content(content):
    """Convert SDK block objects to JSON-serializable dicts."""
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if hasattr(block, "model_dump"):
            out.append(block.model_dump(mode="json"))
        else:
            out.append(block)
    return out


def _transcribe_bytes(audio_bytes: bytes, mime: str) -> str:
    """Denoise + normalize phone audio, then run whisper-large.

    Speed-vs-accuracy: we tried Deepgram nova-3 to shave 2-4s but accuracy
    on phone-quality Omani Arabic dropped sharply — callers had to repeat
    almost every utterance. Reverted to whisper-large + ffmpeg denoise,
    which is the proven combo from the morning calls that worked end-to-end.
    The async poll pipeline absorbs the extra latency without dropping calls.
    """
    import tempfile
    from services import audio_preprocess

    suffix = {
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
    }.get(mime, ".mp3")

    cleaned = audio_preprocess.denoise_and_normalize(audio_bytes, suffix=suffix)
    if cleaned is not audio_bytes:
        suffix = ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(cleaned)
        tmp_path = Path(tmp.name)
    try:
        # Bilingual demo: caller might be Arabic-speaking OR English-speaking,
        # so let whisper detect per utterance instead of forcing language=ar.
        # The hallucination filter (is_likely_hallucination) still catches the
        # "Thanks for watching" residue from misdetected silent clips.
        result = deepgram_stt.transcribe_file(
            tmp_path,
            params={
                "model": "whisper-large",
                "detect_language": "true",
                "smart_format": "true",
                "punctuate": "true",
            },
        )
        return deepgram_stt.extract_transcript(result)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
