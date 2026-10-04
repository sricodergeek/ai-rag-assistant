import secrets

import redis

from backend.app.config import REDIS_URL


_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_SESSION_KEY_PREFIX = "session:"


def create_session(user_id) -> str:
    session_id = secrets.token_urlsafe(32)
    _redis.set(
        f"{_SESSION_KEY_PREFIX}{session_id}",
        str(user_id),
        ex=_SESSION_TTL_SECONDS,
    )
    return session_id


def get_user_id_from_session(session_id: str) -> str | None:
    return _redis.get(f"{_SESSION_KEY_PREFIX}{session_id}")


def delete_session(session_id: str) -> None:
    _redis.delete(f"{_SESSION_KEY_PREFIX}{session_id}")
