from http.cookies import SimpleCookie

import pytest
from starlette.responses import Response

from backend.app import session_cookie
from backend.app.config import _parse_session_cookie_secure_setting
from backend.app.session_cookie import (
    clear_oauth_transaction_cookie,
    clear_session_cookie,
    set_oauth_transaction_cookie,
    set_session_cookie,
)


def _session_cookie(response: Response):
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["session_id"]


def test_session_cookie_attributes_and_opaque_value(monkeypatch):
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", False)
    response = Response()
    session_id = "synthetic-opaque-session-id"

    set_session_cookie(response, session_id)
    cookie = _session_cookie(response)

    assert cookie.key == "session_id"
    assert cookie.value == session_id
    assert cookie["httponly"]
    assert cookie["samesite"] == "lax"
    assert cookie["path"] == "/"
    assert not cookie["domain"]
    assert cookie["max-age"] == str(7 * 24 * 60 * 60)
    assert not cookie["secure"]


def test_local_development_cookie_is_not_secure(monkeypatch):
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", False)
    response = Response()

    set_session_cookie(response, "synthetic-local-session")

    assert not _session_cookie(response)["secure"]


def test_production_configuration_enables_secure_cookie(monkeypatch):
    assert _parse_session_cookie_secure_setting("production", "true") is True
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", True)
    response = Response()

    set_session_cookie(response, "synthetic-production-session")

    assert _session_cookie(response)["secure"]


@pytest.mark.parametrize("setting", [None, "", "false", "0", "no", "off"])
def test_production_rejects_missing_or_disabled_secure_cookie(setting):
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        _parse_session_cookie_secure_setting("production", setting)


def test_production_rejects_invalid_secure_cookie_setting():
    with pytest.raises(RuntimeError, match="recognized boolean"):
        _parse_session_cookie_secure_setting("production", "sometimes")


def test_development_defaults_to_non_secure_cookie():
    assert _parse_session_cookie_secure_setting("development", None) is False


def test_oauth_transaction_cookie_uses_central_secure_setting(monkeypatch):
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", True)
    response = Response()

    set_oauth_transaction_cookie(response, "synthetic-transaction-id")
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    transaction_cookie = cookie["oauth_transaction"]

    assert transaction_cookie["secure"]
    assert transaction_cookie["httponly"]
    assert transaction_cookie["samesite"] == "lax"
    assert transaction_cookie["path"] == "/"
    assert transaction_cookie["max-age"] == "600"

    clear_oauth_transaction_cookie(response)
    cleared_cookie = SimpleCookie()
    cleared_cookie.load(response.headers.getlist("set-cookie")[-1])
    assert cleared_cookie["oauth_transaction"]["secure"]


def test_cookie_value_contains_no_user_or_oauth_information(monkeypatch):
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", False)
    response = Response()
    session_id = "synthetic-opaque-cookie-value"
    sensitive_values = (
        "synthetic-user-id",
        "synthetic@example.test",
        "synthetic-google-sub",
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-id-token",
    )

    set_session_cookie(response, session_id)
    cookie_value = _session_cookie(response).value

    assert cookie_value == session_id
    assert all(value not in cookie_value for value in sensitive_values)


def test_clear_session_cookie_expires_session_id(monkeypatch):
    monkeypatch.setattr(session_cookie, "SESSION_COOKIE_SECURE", True)
    response = Response()

    clear_session_cookie(response)
    cookie = _session_cookie(response)

    assert cookie.key == "session_id"
    assert cookie.value == ""
    assert cookie["max-age"] == "0"
    assert cookie["path"] == "/"
    assert not cookie["domain"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "lax"
    assert cookie["secure"]
