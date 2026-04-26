"""Round 2 of voice tuning — aim: faster, warmer, more natural (less AI-y).

Three knobs pushed in the same direction vs round 1:
  - lower stability        → more expressive variance (less robotic)
  - lower similarity_boost → cloned voice breathes more, less rigid
  - higher style           → more warmth/emotion in delivery
  - higher speed           → closer to natural Omani pace

Run:
    python scripts/test_voice_tuning_v2.py

Regenerates samples with three alternative profiles so you can pick the one
that feels most human. After you pick, we lock it into DEFAULT_SETTINGS.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import elevenlabs_tts  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "tts_tuning_v2"

TEXT = (
    "مرحباً بك في عيادة نورا! "
    "أنا سعيدة بسماع صوتك. "
    "كيف أقدر أساعدك اليوم؟"
)

PROFILES = [
    {
        "slug": "E_natural_warm",
        "label": "طبیعی و گرم (میانه)",
        "settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.45,
            "speed": 1.10,
            "use_speaker_boost": True,
        },
    },
    {
        "slug": "F_expressive",
        "label": "احساسی‌تر، سریع‌تر",
        "settings": {
            "stability": 0.30,
            "similarity_boost": 0.70,
            "style": 0.55,
            "speed": 1.12,
            "use_speaker_boost": True,
        },
    },
    {
        "slug": "G_balanced_human",
        "label": "متعادل و انسانی",
        "settings": {
            "stability": 0.40,
            "similarity_boost": 0.78,
            "style": 0.40,
            "speed": 1.08,
            "use_speaker_boost": True,
        },
    },
]


def main() -> None:
    quota = elevenlabs_tts.get_quota()
    print(f"Remaining quota: {quota['remaining']:,} chars")
    print(f"This test uses ~{len(TEXT) * len(PROFILES)} chars.")
    print()

    for p in PROFILES:
        out = OUT_DIR / f"{p['slug']}.mp3"
        t0 = time.time()
        try:
            elevenlabs_tts.synthesize_to_file(
                TEXT, out, settings=p["settings"], use_cache=False
            )
        except Exception as e:
            print(f"  [{p['slug']}] FAILED: {e}")
            continue
        dt = (time.time() - t0) * 1000
        size_kb = out.stat().st_size / 1024
        s = p["settings"]
        print(f"  [{p['slug']}]  {p['label']}")
        print(f"    stability={s['stability']}  sim={s['similarity_boost']}  "
              f"style={s['style']}  speed={s['speed']}")
        print(f"    {dt:.0f}ms, {size_kb:.1f} KB → {out}")
        print()

    print("=" * 60)
    print("Listen in conversations/tts_tuning_v2/ — tell me E / F / G")


if __name__ == "__main__":
    main()
