"""End-to-end voice agent test — no Twilio, no real phone.

Simulates a full phone conversation by feeding the agent scripted caller
turns (as text, to skip STT for speed) and playing back the bot's audio
replies. After each turn you'll see the transcript AND hear what the
bot would say on a real call.

Run:
    python scripts/test_voice_agent.py

Output lands in conversations/voice_test/ — one MP3 per bot turn,
named by turn index so you can listen in order.

For a STT-included test (slower, uses Deepgram quota), pass --with-stt:
    python scripts/test_voice_agent.py --with-stt
which re-transcribes each bot MP3 as if it were caller audio (sanity-
checks the full pipeline but isn't a real conversation).
"""

import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.voice_agent import VoiceSession  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "voice_test"

# A scripted Omani caller walking through a typical booking.
CALLER_SCRIPT = [
    "أهلاً، أبي أحجز موعد",
    "عربي من فضلك",
    "اسمي سارة وأبي أحجز موعد أسنان للسبت",
    "تنظيف وفحص",
    "الساعة إحدى عشرة صباحاً",
    "شكراً، مع السلامة",
]

CALLER_PHONE = "+96890000001"


def main() -> None:
    # Reset: throwaway history file for a clean test.
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    voice_history = REPO_ROOT / "conversations" / "voice" / f"{CALLER_PHONE.replace('+', '')}.json"
    if voice_history.exists():
        voice_history.unlink()

    session = VoiceSession(caller_phone=CALLER_PHONE)

    print("=" * 70)
    print(f"Simulating a call from {CALLER_PHONE}")
    print("=" * 70)

    # Turn 0 — bot greeting.
    print("\n[BOT turn 0] greeting")
    t0 = time.time()
    greeting_audio = session.greeting()
    greeting_path = OUT_DIR / "00_bot_greeting.mp3"
    greeting_path.write_bytes(greeting_audio)
    print(f"  saved → {greeting_path}  ({(time.time()-t0)*1000:.0f}ms)")

    # Turns 1..N — caller → bot.
    for idx, caller_text in enumerate(CALLER_SCRIPT, start=1):
        print(f"\n[CALLER turn {idx}] {caller_text}")
        t0 = time.time()
        try:
            result = session.respond_to_text(caller_text)
        except Exception as e:
            print(f"  !! agent error: {e}")
            continue
        dt = (time.time() - t0) * 1000
        print(f"[BOT turn {idx}] {result['reply_text']}")
        audio_path = OUT_DIR / f"{idx:02}_bot.mp3"
        audio_path.write_bytes(result["audio"]) if result["audio"] else None
        size_kb = audio_path.stat().st_size / 1024 if audio_path.exists() else 0
        print(f"  saved → {audio_path}  ({dt:.0f}ms, {size_kb:.1f} KB)")

    print("\n" + "=" * 70)
    print(f"Done. Listen in order: {OUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
