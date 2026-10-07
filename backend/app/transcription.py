"""Original-language transcription; audio is used only for this request."""

from openai import OpenAI

from backend.app.config import OPENAI_API_KEY


MAX_AUDIO_BYTES = 10 * 1024 * 1024
client = OpenAI(api_key=OPENAI_API_KEY, timeout=60.0, max_retries=1)


def audio_format(filename: str, data: bytes) -> str | None:
    """Check extension and container signature before sending an upload."""
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension == "webm" and data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if extension in {"mp4", "m4a"} and data[4:8] == b"ftyp":
        return extension
    if extension == "wav" and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if extension in {"mp3", "mpeg", "mpga"} and (
        data.startswith(b"ID3")
        or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    ):
        return extension
    return None


def transcribe_audio(data: bytes, extension: str) -> dict:
    response = client.audio.transcriptions.create(
        model="gpt-transcribe",
        file=(f"recording.{extension}", data),
    )
    text = response.text.strip()
    if not text:
        raise ValueError("No speech was detected. Please try again.")
    # SDK versions may expose extra response fields through model_dump().
    metadata = response.model_dump().get("languages")
    languages = None
    if isinstance(metadata, list):
        languages = [
            item["code"] for item in metadata
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        ]
    return {"text": text, "languages": languages}
