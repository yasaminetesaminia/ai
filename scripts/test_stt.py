"""Smoke test: feed the TTS-generated MP3s back into Deepgram and verify
that the round-trip (text → ElevenLabs → MP3 → Deepgram → text) recovers
the original text.

Run:
    python scripts/test_stt.py

This proves both halves of the voice pipeline work end-to-end before we
wire up Twilio. Any mismatch between original and recovered text is a
first look at how well Deepgram hears Omani Arabic through Noura.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import deepgram_stt  # noqa: E402

# Originals used to generate the MP3s in scripts/test_voice.py
ORIGINALS = {
    "01_welcome":    "مرحباً بك في عيادة نورا. كيف أقدر أساعدك اليوم؟",
    "02_offer_slot": "عندنا موعد متوفر يوم السبت الساعة عشرة صباحاً. هل يناسبك؟",
    "03_confirm":    "تمام، حجزنا لك الموعد. شكراً لاختيارك عيادتنا.",
    "04_english":    "Hello! Welcome to Noora Clinic. How can I help you today?",
    "05_mixed":      "أهلاً! Your appointment is booked for Saturday at 10 AM. شكراً!",
}

AUDIO_DIR = REPO_ROOT / "conversations" / "tts_test"


def main() -> None:
    print("=" * 70)
    print("Testing Deepgram STT against ElevenLabs-generated MP3s")
    print("=" * 70)

    for slug, original in ORIGINALS.items():
        audio = AUDIO_DIR / f"{slug}.mp3"
        if not audio.exists():
            print(f"\n[{slug}] SKIP — {audio} not found. Run test_voice.py first.")
            continue

        t0 = time.time()
        try:
            result = deepgram_stt.transcribe_file(audio)
        except Exception as e:
            print(f"\n[{slug}] FAILED: {e}")
            continue
        dt = (time.time() - t0) * 1000

        transcript = deepgram_stt.extract_transcript(result)
        lang = deepgram_stt.extract_detected_language(result)

        print(f"\n[{slug}]  ({dt:.0f}ms, detected language: {lang})")
        print(f"  expected:   {original}")
        print(f"  transcribed: {transcript}")


if __name__ == "__main__":
    main()
