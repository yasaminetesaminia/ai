"""Optional audio preprocessing before STT.

Phone audio comes in degraded — narrow band (8 kHz mu-law), often with
hum, line noise, or wildly varying volume. Running it through a quick
ffmpeg pipeline before handing it to Whisper measurably improves
transcription accuracy:

  - `afftdn=nf=-25` : FFT-based denoise; nf controls noise floor in dB.
  - `loudnorm`      : EBU R128 loudness normalization so a quiet caller
                      lands at the same perceived level as a loud one.

If ffmpeg isn't installed (local dev environment, container without it),
the helper logs once and returns the original bytes — STT still runs,
just without the boost.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FFMPEG = shutil.which("ffmpeg")
if not _FFMPEG:
    logger.info("ffmpeg not found — audio preprocessing will be skipped.")


def denoise_and_normalize(audio_bytes: bytes, suffix: str = ".ogg") -> bytes:
    """Return cleaner audio bytes. On any failure, returns the input
    unchanged so STT can still run on the raw recording.
    """
    if not _FFMPEG or not audio_bytes:
        return audio_bytes

    in_path = None
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            in_path = Path(f.name)
        # Output as wav so Whisper gets uncompressed audio.
        out_path = in_path.parent / f"clean_{in_path.stem}.wav"

        cmd = [
            _FFMPEG, "-y", "-loglevel", "error",
            "-i", str(in_path),
            "-af", "afftdn=nf=-25,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "16000",  # whisper prefers 16kHz
            "-ac", "1",      # mono
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            logger.warning(
                "ffmpeg preprocessing failed (%s); using raw audio. stderr=%s",
                result.returncode, result.stderr.decode(errors="ignore")[:200],
            )
            return audio_bytes
        return out_path.read_bytes()
    except Exception as e:
        logger.warning("Audio preprocess exception: %s — using raw audio.", e)
        return audio_bytes
    finally:
        for p in (in_path, out_path):
            if p and p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
