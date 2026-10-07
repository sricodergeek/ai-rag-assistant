"""Per-user UTC calendar-day allowances, shared across backend workers."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import redis

from backend.app.config import REDIS_URL, VOICE_STT_DAILY_LIMIT, VOICE_TTS_DAILY_LIMIT


Feature = Literal["stt", "tts"]
_redis = redis.Redis.from_url(
    REDIS_URL, decode_responses=True, socket_connect_timeout=3, socket_timeout=3,
)
_RESERVE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
if used < 0 then return redis.error_reply('invalid counter') end
if used >= limit then return {0, used} end
used = redis.call('INCR', KEYS[1])
redis.call('EXPIREAT', KEYS[1], ARGV[2])
return {1, used}
"""


@dataclass(frozen=True)
class Usage:
    limit: int
    used: int
    remaining: int
    resets_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class UsageUnavailable(Exception):
    pass


class QuotaExhausted(Exception):
    def __init__(self, feature: Feature, usage: Usage):
        self.feature = feature
        self.usage = usage
        super().__init__("Daily voice quota exhausted.")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _period() -> tuple[str, datetime]:
    now = _utc_now().astimezone(timezone.utc)
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.date().isoformat(), reset


def _limit(feature: Feature) -> int:
    return VOICE_STT_DAILY_LIMIT if feature == "stt" else VOICE_TTS_DAILY_LIMIT


def _usage(feature: Feature, used: int, reset: datetime) -> Usage:
    if used < 0:
        raise ValueError("Invalid counter.")
    limit = _limit(feature)
    return Usage(limit, used, max(0, limit - used), reset.isoformat())


def reserve_voice(user_id, feature: Feature) -> Usage:
    day, reset = _period()
    try:
        accepted, used = _redis.eval(
            _RESERVE, 1, f"voice:{feature}:{user_id}:{day}",
            _limit(feature), int(reset.timestamp()),
        )
        usage = _usage(feature, int(used), reset)
    except Exception:
        raise UsageUnavailable() from None
    if not accepted:
        raise QuotaExhausted(feature, usage)
    return usage


def get_voice_usage(user_id) -> dict[str, dict]:
    day, reset = _period()
    try:
        counts = _redis.mget([f"voice:{feature}:{user_id}:{day}" for feature in ("stt", "tts")])
        return {
            feature: _usage(feature, int(count or 0), reset).to_dict()
            for feature, count in zip(("stt", "tts"), counts, strict=True)
        }
    except Exception:
        raise UsageUnavailable() from None
