"""Generate audio samples that exercise the pronunciation dictionary so
you can hear whether the rules actually fire correctly.

Each sample contains terms the dictionary should fix:
  - Currency abbreviations (OMR)
  - Brand names (ovvocompany, Instagram)
  - Medical terms with diacritics added (بوتوكس, فيلر, ليزر)
  - Doctor names with explicit vowels (الدكتورة سارة)
  - Loanword spacing (واتساب → واتس آب)

Run:
    python scripts/test_pronunciation.py

Listen in conversations/pronunciation_test/.
"""

import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services import elevenlabs_tts  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "pronunciation_test"

SAMPLES = [
    ("01_omr", "البوتوكس بحوالي مية وعشرين OMR للجلسة."),
    ("02_brand", "تابعينا على Instagram على ovvocompany للعروض الجديدة."),
    ("03_doctors", "الدكتورة سارة طبيبة الأسنان، والدكتور حسين والدكتورة أماني للتجميل."),
    ("04_medical_ar", "عندنا بوتوكس، فيلر، وليزر — كلها بأحدث الأجهزة."),
    ("05_location", "موقعنا في مسقط، شارع الغبرة، وعندنا موقف سيارات."),
    ("06_mixed", "حياك الله! نقبل واتساب على نفس الرقم، أو ادخل Instagram للحجز."),
]


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    print(f"Generating {len(SAMPLES)} pronunciation test samples...")
    print()

    for slug, text in SAMPLES:
        out = OUT_DIR / f"{slug}.mp3"
        t0 = time.time()
        try:
            elevenlabs_tts.synthesize_to_file(text, out, use_cache=False)
        except Exception as e:
            print(f"  [{slug}] FAILED: {e}")
            continue
        dt = (time.time() - t0) * 1000
        size_kb = out.stat().st_size / 1024
        print(f"  [{slug}]  {dt:.0f}ms  {size_kb:.1f} KB")
        print(f"    text: {text}")
        print(f"    file: {out}")
        print()

    print("=" * 70)
    print(f"Listen in {OUT_DIR}")


if __name__ == "__main__":
    main()
