import json
from unittest.mock import Mock

import pytest

from backend.app import oauth_state


@pytest.fixture
def fake_redis(monkeypatch):
    client = Mock()
    monkeypatch.setattr(oauth_state, "_redis", client)
    return client


def test_transaction_has_ten_minute_ttl_and_is_consumed_once(fake_redis):
    transaction = oauth_state.create_oauth_transaction()
    key = f"oauth_transaction:{transaction['transaction_id']}"
    payload = {"state": transaction["state"], "nonce": transaction["nonce"]}
    assert all(isinstance(value, str) and value for value in transaction.values())
    assert len(set(transaction.values())) == 3
    fake_redis.set.assert_called_once_with(key, json.dumps(payload), ex=600)
    fake_redis.getdel.side_effect = [json.dumps(payload), None]

    assert oauth_state.consume_oauth_transaction(transaction["transaction_id"]) == payload
    assert oauth_state.consume_oauth_transaction(transaction["transaction_id"]) is None
    assert fake_redis.getdel.call_count == 2
    fake_redis.getdel.assert_called_with(key)


def test_legacy_state_preserves_prefix_ttl_and_single_use(fake_redis):
    state = oauth_state.create_oauth_state()
    assert isinstance(state, str) and state
    key = f"oauth_state:{state}"
    fake_redis.set.assert_called_once_with(key, "1", ex=600)
    fake_redis.delete.side_effect = [1, 0]

    assert oauth_state.validate_oauth_state(state) is True
    assert oauth_state.validate_oauth_state(state) is False
    fake_redis.delete.assert_called_with(key)


@pytest.mark.parametrize("payload", [None, "invalid-json", "[]", "{}", '{"state": 1, "nonce": "n"}', '{"state": "s", "nonce": 1}'])
def test_invalid_transaction_returns_none(fake_redis, payload):
    fake_redis.getdel.return_value = payload
    assert oauth_state.consume_oauth_transaction("missing-or-invalid") is None


def test_transaction_redis_error_propagates(fake_redis):
    fake_redis.getdel.side_effect = RuntimeError("Redis unavailable")
    with pytest.raises(RuntimeError, match="Redis unavailable"):
        oauth_state.consume_oauth_transaction("transaction")
