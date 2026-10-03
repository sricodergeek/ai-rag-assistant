import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app import auth, main
from backend.app.database import get_db
from backend.app.models import User


@pytest.fixture
def database(monkeypatch):
    db = Mock()

    def override_get_db():
        yield db

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_get_db)
    return db


def test_auth_me_without_session_cookie_returns_401(monkeypatch, database):
    lookup_session = Mock()
    monkeypatch.setattr(auth, "get_user_id_from_session", lookup_session)

    with TestClient(main.app) as client:
        response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    lookup_session.assert_not_called()
    database.get.assert_not_called()


def test_auth_me_with_invalid_or_expired_session_returns_401(monkeypatch, database):
    lookup_session = Mock(return_value=None)
    monkeypatch.setattr(auth, "get_user_id_from_session", lookup_session)

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-expired-session")
        response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    lookup_session.assert_called_once_with("synthetic-expired-session")
    database.get.assert_not_called()


def test_auth_me_returns_safe_user_fields_for_valid_session(monkeypatch, database):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="synthetic@example.test",
        name="Synthetic User",
        avatar_url="https://example.test/avatar.png",
        auth_provider="google",
        provider_user_id="synthetic-google-sub",
    )
    monkeypatch.setattr(
        auth, "get_user_id_from_session", Mock(return_value=str(user_id))
    )
    database.get.return_value = user

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user_id),
        "email": "synthetic@example.test",
        "name": "Synthetic User",
        "avatar_url": "https://example.test/avatar.png",
    }
    database.get.assert_called_once_with(User, user_id)
    for sensitive_value in (
        "synthetic-session-id",
        "synthetic-google-sub",
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-id-token",
        "synthetic-client-secret",
    ):
        assert sensitive_value not in response.text


def test_auth_me_with_session_for_missing_user_returns_401(monkeypatch, database):
    user_id = uuid.uuid4()
    monkeypatch.setattr(
        auth, "get_user_id_from_session", Mock(return_value=str(user_id))
    )
    database.get.return_value = None

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    database.get.assert_called_once_with(User, user_id)


def test_auth_me_returns_generic_error_when_redis_fails(monkeypatch, database):
    monkeypatch.setattr(
        auth,
        "get_user_id_from_session",
        Mock(side_effect=RuntimeError("synthetic Redis internal failure")),
    )

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.get("/auth/me")

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable."}
    assert "synthetic Redis internal failure" not in response.text
    database.get.assert_not_called()


def test_auth_me_returns_generic_error_when_database_fails(monkeypatch, database):
    user_id = uuid.uuid4()
    monkeypatch.setattr(
        auth, "get_user_id_from_session", Mock(return_value=str(user_id))
    )
    database.get.side_effect = RuntimeError("synthetic database internal failure")

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.get("/auth/me")

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not authenticate request."}
    assert "synthetic database internal failure" not in response.text
