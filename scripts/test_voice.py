"""Smoke test for the ElevenLabs TTS module.

Run from the repo root:
    python scripts/test_voice.py

Generates an MP3 for each line in SAMPLES, writes them under
conversations/tts_test/, and prints the path so you can play them.
A second run hits the cache and is instant — that's how you know
caching works.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import elevenlabs_tts  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "tts_test"

SAMPLES = [
    ("01_welcome", "مرحباً بك في عيادة نورا. كيف أقدر أساعدك اليوم؟"),
    ("02_offer_slot", "عندنا موعد متوفر يوم السبت الساعة عشرة صباحاً. هل يناسبك؟"),
    ("03_confirm", "تمام، حجزنا لك الموعد. شكراً لاختيارك عيادتنا."),
    ("04_english", "Hello! Welcome to Noora Clinic. How can I help you today?"),
    ("05_mixed", "أهلاً! Your appointment is booked for Saturday at 10 AM. شكراً!"),
]


def main() -> None:
    print("=" * 60)
    print("Checking ElevenLabs quota...")
    try:
        quota = elevenlabs_tts.get_quota()
        print(f"  Tier:       {quota['tier']}")
        print(f"  Used:       {quota['character_count']:,} chars")
        print(f"  Limit:      {quota['character_limit']:,} chars")
        print(f"  Remaining:  {quota['remaining']:,} chars")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    print()
    print("Generating samples...")
    for slug, text in SAMPLES:
        out = OUT_DIR / f"{slug}.mp3"
        t0 = time.time()
        try:
            path = elevenlabs_tts.synthesize_to_file(text, out)
        except Exception as e:
            print(f"  [{slug}] FAILED: {e}")
            continue
        dt = (time.time() - t0) * 1000
        size_kb = out.stat().st_size / 1024
        cached = " (cached)" if dt < 50 else ""
        print(f"  [{slug}] {dt:6.0f}ms  {size_kb:5.1f} KB{cached}  →  {path}")

    print()
    print("Done. Open the MP3s in conversations/tts_test/ to listen.")


if __name__ == "__main__":
    main()
