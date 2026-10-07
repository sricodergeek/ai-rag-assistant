import math
import os
from pathlib import Path

from dotenv import load_dotenv


_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_ROOT_ENV)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def _voice_limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise RuntimeError(f"{name} must be a non-negative integer.") from None
    if value < 0:
        raise RuntimeError(f"{name} must be a non-negative integer.")
    return value


VOICE_STT_DAILY_LIMIT = _voice_limit("VOICE_STT_DAILY_LIMIT", 10)
VOICE_TTS_DAILY_LIMIT = _voice_limit("VOICE_TTS_DAILY_LIMIT", 5)
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts").strip() or "gpt-4o-mini-tts"
TTS_VOICE = os.getenv("TTS_VOICE", "marin").strip() or "marin"
TTS_FORMAT = os.getenv("TTS_FORMAT", "mp3").strip().lower() or "mp3"
if TTS_FORMAT not in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
    raise RuntimeError("TTS_FORMAT must be a supported audio format.")

_SECURE_COOKIE_TRUE_VALUES = {"1", "true", "yes", "on"}
_SECURE_COOKIE_FALSE_VALUES = {"0", "false", "no", "off"}
_PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def _parse_session_cookie_secure_setting(
    environment: str, setting: str | None
) -> bool:
    normalized_environment = environment.strip().lower()
    if setting is None or not setting.strip():
        if normalized_environment in _PRODUCTION_ENVIRONMENTS:
            raise RuntimeError(
                "SESSION_COOKIE_SECURE must be explicitly enabled in production."
            )
        return False

    normalized_setting = setting.strip().lower()
    if normalized_setting in _SECURE_COOKIE_TRUE_VALUES:
        return True
    if normalized_setting in _SECURE_COOKIE_FALSE_VALUES:
        if normalized_environment in _PRODUCTION_ENVIRONMENTS:
            raise RuntimeError(
                "SESSION_COOKIE_SECURE must be enabled in production."
            )
        return False

    raise RuntimeError(
        "SESSION_COOKIE_SECURE must be a recognized boolean value."
    )


def _parse_rag_max_distance(setting: str | None) -> float | None:
    if setting is None or not setting.strip():
        return None
    try:
        distance = float(setting)
    except ValueError as error:
        raise RuntimeError("RAG_MAX_DISTANCE must be a finite numeric value.") from error
    if not math.isfinite(distance):
        raise RuntimeError("RAG_MAX_DISTANCE must be a finite numeric value.")
    return distance


def _parse_rag_max_history_messages(setting: str | None) -> int:
    if setting is None or not setting.strip():
        return 10
    try:
        message_count = int(setting)
    except ValueError as error:
        raise RuntimeError("RAG_MAX_HISTORY_MESSAGES must be a positive integer.") from error
    if message_count <= 0:
        raise RuntimeError("RAG_MAX_HISTORY_MESSAGES must be a positive integer.")
    return message_count


RAG_MAX_DISTANCE = _parse_rag_max_distance(os.getenv("RAG_MAX_DISTANCE"))
RAG_MAX_HISTORY_MESSAGES = _parse_rag_max_history_messages(
    os.getenv("RAG_MAX_HISTORY_MESSAGES")
)

SESSION_COOKIE_SECURE = _parse_session_cookie_secure_setting(
    ENVIRONMENT, os.getenv("SESSION_COOKIE_SECURE")
)

if not OPENAI_API_KEY or not OPENAI_API_KEY.strip():
    raise RuntimeError(
        f"OPENAI_API_KEY is missing or empty. Set it in the root .env file ({_ROOT_ENV})."
    )
