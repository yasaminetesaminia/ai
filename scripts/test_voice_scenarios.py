"""Run the voice agent through six realistic scenarios and save each
bot reply as an MP3 so you can listen in order.

Scenarios covered:
  1. New booking (Arabic, dental)
  2. Cancel (Arabic)
  3. Reschedule (Arabic)
  4. Services inquiry (Arabic — general then specific)
  5. Pricing (Arabic — asking about prices before booking)
  6. Clinic info (mixed / English — location, parking, hours)

Each scenario uses a unique phone number so their conversation histories
don't cross-contaminate. Output lands in conversations/voice_scenarios/.

Run:
    python scripts/test_voice_scenarios.py
"""

import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.voice_agent import VoiceSession  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "voice_scenarios"
VOICE_HISTORY_DIR = REPO_ROOT / "conversations" / "voice"

SCENARIOS = [
    {
        "slug": "1_new_booking_ar",
        "caller": "+96891000001",
        "title": "حجز موعد جديد — طب الأسنان",
        "turns": [
            "حياك الله، أبي أحجز موعد فحص أسنان",
            "السبت الجاي الصبح",
            "الساعة عشر زين",
            "سارة الهنائية، رقمي ذا",
            "ماشي، تمام",
        ],
    },
    {
        "slug": "2_cancel_ar",
        "caller": "+96891000002",
        "title": "إلغاء موعد",
        "turns": [
            "السلام عليكم، أبي ألغي موعدي",
            "إيوه أبي ألغيه",
            "مشكورة",
        ],
    },
    {
        "slug": "3_reschedule_ar",
        "caller": "+96891000003",
        "title": "تغيير موعد",
        "turns": [
            "مرحبا، أبي أغيّر موعدي من السبت إلى الأحد",
            "الصبح إذا يصير",
            "إحدى عشر ماشي",
            "تمام، مشكورة",
        ],
    },
    {
        "slug": "4_services_inquiry_ar",
        "caller": "+96891000004",
        "title": "سؤال عن الخدمات",
        "turns": [
            "شنو الخدمات اللي عندكم؟",
            "التجميل، شنو فيه؟",
            "إيش هو البوتوكس بالضبط؟",
            "زين، أدرّس فيه وأرجع لكم. مشكورة",
        ],
    },
    {
        "slug": "5_pricing_ar",
        "caller": "+96891000005",
        "title": "سؤال عن الأسعار",
        "turns": [
            "كم سعر فحص الأسنان؟",
            "والحشو؟",
            "وبوتوكس كم؟",
            "وليزر البكيني؟",
            "في باقات للليزر؟",
            "تمام شكراً",
        ],
    },
    {
        "slug": "6_clinic_info_en",
        "caller": "+96891000006",
        "title": "Clinic info (mixed EN/AR)",
        "turns": [
            "Hi, where is your clinic located?",
            "Do you have parking?",
            "What are your hours?",
            "Do you have a website or Instagram?",
            "Thanks!",
        ],
    },
]


def _reset_history(caller_phone: str) -> None:
    safe = caller_phone.replace("+", "")
    path = VOICE_HISTORY_DIR / f"{safe}.json"
    if path.exists():
        path.unlink()


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    for scenario in SCENARIOS:
        print("\n" + "=" * 70)
        print(f"SCENARIO {scenario['slug']}: {scenario['title']}")
        print(f"Caller: {scenario['caller']}")
        print("=" * 70)

        _reset_history(scenario["caller"])
        scenario_dir = OUT_DIR / scenario["slug"]
        scenario_dir.mkdir()

        session = VoiceSession(caller_phone=scenario["caller"])

        # Turn 0 — greeting (only for first scenario to save quota, but
        # actually every new call should greet — keep it on).
        t0 = time.time()
        greeting = session.greeting()
        (scenario_dir / "00_bot_greeting.mp3").write_bytes(greeting)
        print(f"  [BOT 00] greeting  ({(time.time()-t0)*1000:.0f}ms)")

        for idx, caller_text in enumerate(scenario["turns"], start=1):
            print(f"\n  [CALLER {idx:02}] {caller_text}")
            t0 = time.time()
            try:
                result = session.respond_to_text(caller_text)
            except Exception as e:
                print(f"    !! error: {e}")
                continue
            dt = (time.time() - t0) * 1000
            reply = result["reply_text"] or "(no text)"
            print(f"  [BOT    {idx:02}] {reply}")
            if result["audio"]:
                (scenario_dir / f"{idx:02}_bot.mp3").write_bytes(result["audio"])
            print(f"    ({dt:.0f}ms)")

    print("\n" + "=" * 70)
    print(f"All scenarios saved under: {OUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
