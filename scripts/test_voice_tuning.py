"""A/B test of voice settings: same Arabic sentence, four presets.

Run:
    python scripts/test_voice_tuning.py

Generates the same greeting with four different profiles so you can pick
which one sounds best. Each profile explores a different part of the
settings space — stability (expressiveness) vs style (warmth) vs speed.
After you pick a winner, we lock those numbers into DEFAULT_SETTINGS.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import elevenlabs_tts  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "tts_tuning"

# One warm, natural receptionist line — enough to hear tone and pacing.
TEXT = (
    "مرحباً بك في عيادة نورا! "
    "أنا سعيدة بسماع صوتك. "
    "كيف أقدر أساعدك اليوم؟"
)

PROFILES = [
    {
        "slug": "A_current_warm",
        "label": "فعلی - گرم و طبیعی (stability=0.35, style=0.45, speed=1.08)",
        "settings": {
            "stability": 0.35,
            "similarity_boost": 0.80,
            "style": 0.45,
            "speed": 1.08,
            "use_speaker_boost": True,
        },
    },
    {
        "slug": "B_very_expressive",
        "label": "خیلی احساسی (stability=0.25, style=0.65, speed=1.10)",
        "settings": {
            "stability": 0.25,
            "similarity_boost": 0.75,
            "style": 0.65,
            "speed": 1.10,
            "use_speaker_boost": True,
        },
    },
    {
        "slug": "C_fast_friendly",
        "label": "سریع و دوستانه (stability=0.40, style=0.35, speed=1.15)",
        "settings": {
            "stability": 0.40,
            "similarity_boost": 0.82,
            "style": 0.35,
            "speed": 1.15,
            "use_speaker_boost": True,
        },
    },
    {
        "slug": "D_smooth_conversational",
        "label": "آرام و حرفه‌ای (stability=0.45, style=0.30, speed=1.05)",
        "settings": {
            "stability": 0.45,
            "similarity_boost": 0.85,
            "style": 0.30,
            "speed": 1.05,
            "use_speaker_boost": True,
        },
    },
]


def main() -> None:
    quota = elevenlabs_tts.get_quota()
    print(f"Remaining quota: {quota['remaining']:,} chars")
    print(f"This test uses ~{len(TEXT) * len(PROFILES)} chars total.")
    print()

    for p in PROFILES:
        out = OUT_DIR / f"{p['slug']}.mp3"
        t0 = time.time()
        # use_cache=False so each profile hits the API (different settings).
        try:
            elevenlabs_tts.synthesize_to_file(
                TEXT, out, settings=p["settings"], use_cache=False
            )
        except Exception as e:
            print(f"  [{p['slug']}] FAILED: {e}")
            continue
        dt = (time.time() - t0) * 1000
        size_kb = out.stat().st_size / 1024
        print(f"  [{p['slug']}]")
        print(f"    {p['label']}")
        print(f"    {dt:.0f}ms, {size_kb:.1f} KB → {out}")
        print()

    print("=" * 60)
    print("Listen to all four in conversations/tts_tuning/")
    print("Tell me which slug (A / B / C / D) sounds best.")


if __name__ == "__main__":
    main()
