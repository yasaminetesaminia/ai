"""Local end-to-end test of the Twilio voice endpoints — no real Twilio,
no real phone, no public URL needed.

What it does:
  1. Starts the Flask app via the test client
  2. Hits /voice/incoming as if Twilio called it (POST with From=...)
  3. Parses the returned TwiML to find the audio URL Twilio would play
  4. Hits /voice/audio/<id> and saves the MP3 (the bot's greeting)
  5. Reads a sample caller MP3 (an existing tts_test file) and POSTs to
     /voice/respond as if Twilio uploaded a recording — but since the
     real path downloads from Twilio, we monkey-patch download_recording
     to return our local sample bytes
  6. Saves each reply MP3 so you can listen and verify the full loop

Run:
    python scripts/test_twilio_endpoints.py
"""

import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force test-mode Twilio config so is_configured() returns True without
# needing a real account.
import os
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
os.environ["TWILIO_AUTH_TOKEN"] = "testtoken"
os.environ["TWILIO_PHONE_NUMBER"] = "+15555550100"
os.environ["TWILIO_PUBLIC_BASE_URL"] = "http://localhost:5000"

import config  # noqa: E402  (re-import after env tweaks)
config.TWILIO_ACCOUNT_SID = "ACtest"
config.TWILIO_AUTH_TOKEN = "testtoken"
config.TWILIO_PUBLIC_BASE_URL = "http://localhost:5000"

import app as flask_app  # noqa: E402
from services import twilio_voice  # noqa: E402

OUT_DIR = REPO_ROOT / "conversations" / "twilio_test"

CALLER_PHONE = "+96891000099"

# Pre-recorded "caller" MP3s. We'll feed each one in turn as if the caller
# spoke it. Pick samples we know whisper-large transcribes correctly.
SAMPLE_TURNS = [
    REPO_ROOT / "conversations" / "tts_test" / "01_welcome.mp3",
    REPO_ROOT / "conversations" / "tts_test" / "02_offer_slot.mp3",
    REPO_ROOT / "conversations" / "tts_test" / "03_confirm.mp3",
]


def _twiml_play_url(twiml: str) -> str | None:
    m = re.search(r"<Play>([^<]+)</Play>", twiml)
    return m.group(1) if m else None


def _save_audio_from_url(client, url: str, dst: Path) -> int:
    """Pull the audio from the in-process Flask app and save to disk."""
    path = url.replace("http://localhost:5000", "")
    resp = client.get(path)
    if resp.status_code != 200:
        print(f"    ! audio fetch failed: {resp.status_code}")
        return 0
    dst.write_bytes(resp.data)
    return len(resp.data)


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    # Reset history so the test is reproducible.
    history_file = REPO_ROOT / "conversations" / "voice" / f"{CALLER_PHONE.replace('+', '')}.json"
    if history_file.exists():
        history_file.unlink()

    # Stub out download_recording to return our local sample bytes.
    sample_iter = iter(SAMPLE_TURNS)
    def fake_download(_url: str) -> bytes:
        try:
            path = next(sample_iter)
        except StopIteration:
            return b""
        return path.read_bytes()
    twilio_voice.download_recording = fake_download

    client = flask_app.app.test_client()

    print("=" * 70)
    print(f"Simulating a Twilio call from {CALLER_PHONE}")
    print("=" * 70)

    # ---- 1. Incoming call ----
    print("\n[1] POST /voice/incoming")
    resp = client.post("/voice/incoming", data={"From": CALLER_PHONE, "CallSid": "CA-test"})
    print(f"    status={resp.status_code}")
    twiml = resp.data.decode("utf-8")
    print(f"    twiml={twiml[:200]}...")
    audio_url = _twiml_play_url(twiml)
    if audio_url:
        size = _save_audio_from_url(client, audio_url, OUT_DIR / "00_greeting.mp3")
        print(f"    saved greeting → {OUT_DIR / '00_greeting.mp3'} ({size/1024:.1f} KB)")

    # ---- 2..N. Per-turn responses ----
    for idx, sample in enumerate(SAMPLE_TURNS, start=1):
        print(f"\n[{idx+1}] POST /voice/respond  (simulating caller said the audio in {sample.name})")
        resp = client.post(
            "/voice/respond",
            data={
                "From": CALLER_PHONE,
                "CallSid": "CA-test",
                "RecordingUrl": "http://example.com/fake-recording",
                "RecordingSid": f"RE-{idx}",
            },
        )
        print(f"    status={resp.status_code}")
        twiml = resp.data.decode("utf-8")
        print(f"    twiml[:250]={twiml[:250]}...")
        audio_url = _twiml_play_url(twiml)
        if audio_url:
            size = _save_audio_from_url(client, audio_url, OUT_DIR / f"{idx:02}_bot.mp3")
            print(f"    saved bot reply → {OUT_DIR}/{idx:02}_bot.mp3 ({size/1024:.1f} KB)")
        else:
            print("    no <Play> in TwiML (probably the goodbye/hangup branch)")

    print("\n" + "=" * 70)
    print(f"Done. Check {OUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
