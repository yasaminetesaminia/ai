"""Pre-generate the most common bot phrases so live phone calls hit the
TTS cache immediately instead of waiting on ElevenLabs (~1-2s per call).

Generated phrases stay on disk under conversations/tts_cache/ so all
future synthesize() calls for these exact strings return instantly.

Run once after each prompt change or voice swap:
    python scripts/prewarm_tts.py
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import elevenlabs_tts  # noqa: E402

# Phrases the voice agent emits constantly. Pre-cache them so callers
# never wait on these. Keep this list small — only repeat-traffic phrases.
COMMON_PHRASES = [
    # Bilingual greeting (most-played phrase by far)
    "أهلاً بك في عيادة نورا، كيف ممكن أساعدك؟ Welcome to Noora Clinic, how can I help you?",
    # Re-prompts when STT mishears
    "آسفة، ما سمعت. ممكن تعيدي؟",
    "آسفة، ممكن تعيدي؟",
    # Common openers and acknowledgements
    "حياك الله! إيش أقدر أسوي لك اليوم؟",
    "تمام، لحظة من فضلك.",
    "دقيقة، أتأكد لك.",
    "في أمان الله!",
    "مشكورة يا غالية، في أمان الله.",
    "إيش الخدمة اللي تبين؟ عندنا طب الأسنان، الليزر، التنحيف، والتجميل.",
    "تمام، أحجز لك الموعد. لحظة.",
]


def main() -> None:
    print(f"Pre-warming {len(COMMON_PHRASES)} phrases...")
    print()
    saved = 0
    for phrase in COMMON_PHRASES:
        t0 = time.time()
        try:
            elevenlabs_tts.synthesize(phrase)  # writes to disk cache
        except Exception as e:
            print(f"  FAIL: {phrase[:50]}... -> {e}")
            continue
        dt = (time.time() - t0) * 1000
        cached = "(cached)" if dt < 50 else f"({dt:.0f}ms)"
        print(f"  {cached:>10}  {phrase}")
        if dt >= 50:
            saved += 1

    print()
    print(f"Done. {saved} new phrase(s) generated, {len(COMMON_PHRASES) - saved} were already cached.")
    print("Live calls will now play these instantly.")


if __name__ == "__main__":
    main()
