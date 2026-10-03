import uuid

import pytest

from backend.app import session_service


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def set(self, key, value, ex):
        self.values[key] = value
        self.expirations[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def ttl(self, key):
        return self.expirations.get(key, -2)

    def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)


@pytest.fixture
def fake_redis(monkeypatch):
    connection = FakeRedis()
    monkeypatch.setattr(session_service, "_redis", connection)
    return connection


def test_create_session_returns_nonempty_opaque_id_and_maps_user(fake_redis):
    user_id = str(uuid.uuid4())

    session_id = session_service.create_session(user_id)

    assert isinstance(session_id, str)
    assert session_id
    assert user_id not in session_id
    assert fake_redis.get(f"session:{session_id}") == user_id
    assert session_service.get_user_id_from_session(session_id) == user_id


def test_session_ttl_is_seven_days(fake_redis):
    session_id = session_service.create_session(str(uuid.uuid4()))

    assert fake_redis.ttl(f"session:{session_id}") == 7 * 24 * 60 * 60


def test_missing_session_returns_none(fake_redis):
    assert session_service.get_user_id_from_session("missing-session") is None


def test_delete_session_removes_session_and_is_safe_when_missing(fake_redis):
    session_id = session_service.create_session(str(uuid.uuid4()))

    assert session_service.get_user_id_from_session(session_id) is not None
    assert session_service.delete_session(session_id) is None
    assert session_service.get_user_id_from_session(session_id) is None
    assert session_service.delete_session(session_id) is None


def test_same_user_receives_distinct_session_ids(fake_redis):
    user_id = str(uuid.uuid4())

    first_session_id = session_service.create_session(user_id)
    second_session_id = session_service.create_session(user_id)

    assert first_session_id != second_session_id
    assert fake_redis.get(f"session:{first_session_id}") == user_id
    assert fake_redis.get(f"session:{second_session_id}") == user_id
