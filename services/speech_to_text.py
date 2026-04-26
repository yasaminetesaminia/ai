import io
from openai import OpenAI
import config


client = OpenAI(api_key=config.OPENAI_API_KEY)


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe audio bytes to text using OpenAI Whisper API."""
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"

    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )
    return transcript.text
