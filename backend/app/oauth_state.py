import json
import secrets

import redis

from backend.app.config import REDIS_URL


_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
_STATE_TTL_SECONDS = 10 * 60
_STATE_KEY_PREFIX = "oauth_state:"
_TRANSACTION_TTL_SECONDS = 10 * 60
_TRANSACTION_KEY_PREFIX = "oauth_transaction:"


def create_oauth_state() -> str:
    state = secrets.token_urlsafe(32)
    _redis.set(f"{_STATE_KEY_PREFIX}{state}", "1", ex=_STATE_TTL_SECONDS)
    return state


def validate_oauth_state(state: str) -> bool:
    return _redis.delete(f"{_STATE_KEY_PREFIX}{state}") == 1


def create_oauth_transaction() -> dict[str, str]:
    transaction_id = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    transaction = {"state": state, "nonce": nonce}
    _redis.set(
        f"{_TRANSACTION_KEY_PREFIX}{transaction_id}",
        json.dumps(transaction),
        ex=_TRANSACTION_TTL_SECONDS,
    )
    return {"transaction_id": transaction_id, **transaction}


def consume_oauth_transaction(transaction_id: str) -> dict[str, str] | None:
    """Atomically consume and return a browser-bound OAuth transaction."""
    serialized_transaction = _redis.getdel(
        f"{_TRANSACTION_KEY_PREFIX}{transaction_id}"
    )
    if serialized_transaction is None:
        return None

    try:
        transaction = json.loads(serialized_transaction)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(transaction, dict):
        return None
    if not isinstance(transaction.get("state"), str):
        return None
    if not isinstance(transaction.get("nonce"), str):
        return None
    return transaction
