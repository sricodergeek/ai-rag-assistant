from http.cookies import SimpleCookie
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.app import auth, main


def _assert_session_cookie_cleared(response):
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    session_cookie = cookie["session_id"]
    assert session_cookie.value == ""
    assert session_cookie["max-age"] == "0"
    assert session_cookie["path"] == "/"
    assert session_cookie["httponly"]
    assert session_cookie["samesite"] == "lax"


def test_logout_deletes_redis_session_and_clears_cookie(monkeypatch):
    session_id = "synthetic-opaque-session-id"
    delete_session = Mock(return_value=None)
    monkeypatch.setattr(auth, "delete_session", delete_session)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    with TestClient(main.app) as client:
        client.cookies.set("session_id", session_id)
        response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    delete_session.assert_called_once_with(session_id)
    _assert_session_cookie_cleared(response)
    assert session_id not in response.text


def test_logout_without_cookie_succeeds_and_clears_cookie(monkeypatch):
    delete_session = Mock()
    monkeypatch.setattr(auth, "delete_session", delete_session)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    with TestClient(main.app) as client:
        response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    delete_session.assert_not_called()
    _assert_session_cookie_cleared(response)


def test_logout_with_nonexistent_session_succeeds(monkeypatch):
    session_id = "synthetic-expired-session-id"
    delete_session = Mock(return_value=None)
    monkeypatch.setattr(auth, "delete_session", delete_session)

    with TestClient(main.app) as client:
        client.cookies.set("session_id", session_id)
        response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    delete_session.assert_called_once_with(session_id)
    _assert_session_cookie_cleared(response)


def test_logout_redis_failure_returns_generic_error(monkeypatch):
    delete_session = Mock(side_effect=RuntimeError("synthetic Redis internal detail"))
    monkeypatch.setattr(auth, "delete_session", delete_session)

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.post("/auth/logout")

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not complete logout."}
    assert "synthetic Redis internal detail" not in response.text
    assert "synthetic-session-id" not in response.text


def test_logout_response_does_not_expose_session_or_oauth_tokens(monkeypatch):
    monkeypatch.setattr(auth, "delete_session", Mock(return_value=None))
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    sensitive_values = (
        "synthetic-session-id",
        "synthetic-user-id",
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-id-token",
    )

    with TestClient(main.app) as client:
        client.cookies.set("session_id", "synthetic-session-id")
        response = client.post("/auth/logout")

    assert response.status_code == 200
    for value in sensitive_values:
        assert value not in response.text
