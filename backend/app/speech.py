"""On-demand speech generation; no translation or persistent audio files."""

from openai import OpenAI

from backend.app.config import OPENAI_API_KEY, TTS_FORMAT, TTS_MODEL, TTS_VOICE


MAX_SPEECH_CHARACTERS = 4096
client = OpenAI(api_key=OPENAI_API_KEY, timeout=60.0, max_retries=1)
_CONTENT_TYPES = {
    "mp3": "audio/mpeg", "opus": "audio/ogg", "aac": "audio/aac",
    "flac": "audio/flac", "wav": "audio/wav", "pcm": "application/octet-stream",
}


def validate_speech_text(text: str) -> None:
    if not text.strip():
        raise ValueError("Speech text cannot be empty.")
    if len(text) > MAX_SPEECH_CHARACTERS:
        raise ValueError("Voice answers are limited to 4,096 characters. The full text remains available.")


def generate_speech(text: str) -> tuple[bytes, str]:
    validate_speech_text(text)
    with client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL, voice=TTS_VOICE, input=text, response_format=TTS_FORMAT,
    ) as response:
        audio = response.read()
    if not audio:
        raise RuntimeError("Empty speech response.")
    return audio, _CONTENT_TYPES[TTS_FORMAT]
