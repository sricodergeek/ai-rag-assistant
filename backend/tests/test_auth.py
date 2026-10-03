import io
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock, call
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.app import auth, main
from backend.app import session_cookie
from backend.app.database import get_db
from backend.app import oauth_state


@pytest.fixture(autouse=True)
def mock_database_dependency(monkeypatch):
    database = object()

    def override_get_db():
        yield database

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setattr(
        auth,
        "consume_oauth_transaction",
        Mock(return_value={"state": "synthetic-state", "nonce": "synthetic-nonce"}),
    )
    return database


def _token_response(body: bytes, status: int = 200):
    response = Mock()
    response.status = status
    response.read.return_value = body
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def _successful_exchange_body():
    return (
        b'{"access_token":"synthetic-access-token",'
        b'"refresh_token":"synthetic-refresh-token",'
        b'"id_token":"synthetic-id-token"}'
    )


def _callback(
    client,
    *,
    code="synthetic-code",
    state="synthetic-state",
    transaction_id="synthetic-transaction-id",
    error=None,
):
    client.cookies.set("oauth_transaction", transaction_id)
    try:
        params = {"state": state}
        if code is not None:
            params["code"] = code
        if error is not None:
            params["error"] = error
        return client.get(
            "/auth/google/callback",
            params=params,
        )
    finally:
        client.cookies.delete("oauth_transaction")


def test_google_callback_success_completes_login_without_exposing_tokens(
    monkeypatch, mock_database_dependency
):
    events = []
    identity = {
        "sub": "synthetic-google-sub",
        "email": "synthetic@example.test",
        "name": "Synthetic User",
    }
    user = SimpleNamespace(id="synthetic-user-uuid")
    session_id = "synthetic-opaque-session-id"
    transaction_id = "synthetic-transaction-id"
    expected_state = "synthetic-state"
    expected_nonce = "synthetic-nonce"

    def consume_transaction(received_transaction_id):
        events.append(("transaction", received_transaction_id))
        assert received_transaction_id == transaction_id
        return {"state": expected_state, "nonce": expected_nonce}

    def open_token_request(request, timeout):
        events.append(("exchange", request.full_url))
        assert timeout == 10
        assert request.full_url == "https://oauth2.googleapis.com/token"
        form_data = parse_qs(request.data.decode("utf-8"))
        assert form_data == {
            "code": ["synthetic-code"],
            "client_id": ["synthetic-client-id"],
            "client_secret": ["synthetic-client-secret"],
            "redirect_uri": ["https://example.test/callback"],
            "grant_type": ["authorization_code"],
        }
        return _token_response(_successful_exchange_body())

    def verify_id_token(token, nonce):
        events.append(("verify", token, nonce))
        assert nonce == expected_nonce
        return identity

    def get_or_create_user(db, verified_identity):
        events.append(("user", db, verified_identity))
        return user

    def create_redis_session(user_id):
        events.append(("session", user_id))
        return session_id

    original_set_cookie = auth.set_session_cookie

    def set_cookie(response, cookie_session_id):
        events.append(("cookie", cookie_session_id))
        original_set_cookie(response, cookie_session_id)

    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "synthetic-client-id")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "synthetic-client-secret")
    monkeypatch.setattr(auth, "GOOGLE_REDIRECT_URI", "https://example.test/callback")
    monkeypatch.setattr(auth, "FRONTEND_URL", "https://frontend.example.test")
    monkeypatch.setattr(auth, "consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(auth, "urlopen", open_token_request)
    monkeypatch.setattr(auth, "verify_google_id_token", verify_id_token)
    user_service = Mock(side_effect=get_or_create_user)
    monkeypatch.setattr(auth, "get_or_create_google_user", user_service)
    monkeypatch.setattr(auth, "create_session", create_redis_session)
    monkeypatch.setattr(auth, "set_session_cookie", set_cookie)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client, state=expected_state, transaction_id=transaction_id)

    assert response.status_code == 307
    assert response.headers["location"] == "https://frontend.example.test"
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    assert cookie["oauth_transaction"].value == ""
    assert cookie["oauth_transaction"]["max-age"] == "0"
    assert cookie["oauth_transaction"]["httponly"]
    assert cookie["oauth_transaction"]["samesite"] == "lax"
    assert cookie["oauth_transaction"]["path"] == "/"
    assert cookie["session_id"].value == session_id
    assert cookie["session_id"]["httponly"]
    user_service.assert_called_once_with(mock_database_dependency, identity)
    assert events == [
        ("transaction", transaction_id),
        ("exchange", "https://oauth2.googleapis.com/token"),
        ("verify", "synthetic-id-token", expected_nonce),
        ("user", mock_database_dependency, identity),
        ("session", "synthetic-user-uuid"),
        ("cookie", session_id),
    ]
    for sensitive_value in (
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-id-token",
        "synthetic-client-secret",
    ):
        assert sensitive_value not in response.text
        assert sensitive_value not in response.headers["set-cookie"]
    assert expected_state not in response.headers["set-cookie"]
    assert expected_nonce not in response.headers["set-cookie"]
    assert transaction_id not in response.text


def test_unknown_or_expired_transaction_prevents_later_login_steps(monkeypatch):
    monkeypatch.setattr(auth, "consume_oauth_transaction", Mock(return_value=None))
    token_request = Mock()
    verify_token = Mock()
    create_user = Mock()
    create_session = Mock()
    set_cookie = Mock()
    monkeypatch.setattr(auth, "urlopen", token_request)
    monkeypatch.setattr(auth, "verify_google_id_token", verify_token)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)
    monkeypatch.setattr(auth, "set_session_cookie", set_cookie)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client, state="invalid-state")

    assert response.status_code == 400
    token_request.assert_not_called()
    verify_token.assert_not_called()
    create_user.assert_not_called()
    create_session.assert_not_called()
    set_cookie.assert_not_called()


def test_access_denied_consumes_transaction_clears_cookie_and_redirects(
    monkeypatch,
):
    transaction_id = "synthetic-transaction-id"
    consume_transaction = Mock(
        return_value={"state": "synthetic-state", "nonce": "synthetic-nonce"}
    )
    exchange = Mock()
    verify_token = Mock()
    create_user = Mock()
    create_session = Mock()
    monkeypatch.setattr(auth, "FRONTEND_URL", "https://frontend.example.test")
    monkeypatch.setattr(auth, "consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(auth, "urlopen", exchange)
    monkeypatch.setattr(auth, "verify_google_id_token", verify_token)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client, code=None, error="access_denied")

    assert response.status_code == 307
    assert response.headers["location"] == "https://frontend.example.test"
    consume_transaction.assert_called_once_with(transaction_id)
    exchange.assert_not_called()
    verify_token.assert_not_called()
    create_user.assert_not_called()
    create_session.assert_not_called()
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    assert cookies["oauth_transaction"].value == ""
    assert cookies["oauth_transaction"]["max-age"] == "0"
    assert "session_id" not in cookies
    for internal_value in (
        "access_denied",
        "synthetic-state",
        "synthetic-nonce",
        transaction_id,
    ):
        assert internal_value not in response.text
        assert internal_value not in response.headers["location"]
        assert internal_value not in response.headers["set-cookie"]


def test_other_provider_error_is_generic_and_does_not_exchange_code(monkeypatch):
    consume_transaction = Mock(
        return_value={"state": "synthetic-state", "nonce": "synthetic-nonce"}
    )
    exchange = Mock()
    create_user = Mock()
    create_session = Mock()
    monkeypatch.setattr(auth, "consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(auth, "urlopen", exchange)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client, code=None, error="temporarily_unavailable")

    assert response.status_code == 307
    assert response.headers["location"] == auth.FRONTEND_URL
    assert "temporarily_unavailable" not in response.text
    assert "temporarily_unavailable" not in response.headers["location"]
    exchange.assert_not_called()
    create_user.assert_not_called()
    create_session.assert_not_called()


def test_missing_transaction_cookie_rejects_callback_before_oauth_steps(monkeypatch):
    consume_transaction = Mock()
    exchange = Mock()
    create_user = Mock()
    create_session = Mock()
    monkeypatch.setattr(auth, "consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(auth, "urlopen", exchange)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get(
            "/auth/google/callback",
            params={"code": "synthetic-code", "state": "synthetic-state"},
        )

    assert response.status_code == 400
    consume_transaction.assert_not_called()
    exchange.assert_not_called()
    create_user.assert_not_called()
    create_session.assert_not_called()


def test_state_mismatch_consumes_transaction_and_creates_no_user_or_session(monkeypatch):
    monkeypatch.setattr(
        auth,
        "consume_oauth_transaction",
        Mock(return_value={"state": "different-synthetic-state", "nonce": "synthetic-nonce"}),
    )
    exchange = Mock()
    create_user = Mock()
    create_session = Mock()
    monkeypatch.setattr(auth, "urlopen", exchange)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or expired OAuth transaction."}
    exchange.assert_not_called()
    create_user.assert_not_called()
    create_session.assert_not_called()


def test_nonce_mismatch_prevents_user_and_session_creation(monkeypatch):
    monkeypatch.setattr(
        auth,
        "urlopen",
        Mock(return_value=_token_response(_successful_exchange_body())),
    )
    verify_token = Mock(side_effect=ValueError("synthetic nonce mismatch"))
    create_user = Mock()
    create_session = Mock()
    monkeypatch.setattr(auth, "verify_google_id_token", verify_token)
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 400
    assert response.json() == {"detail": "Google ID token is invalid."}
    verify_token.assert_called_once_with("synthetic-id-token", "synthetic-nonce")
    create_user.assert_not_called()
    create_session.assert_not_called()


def test_oauth_transaction_is_consumed_only_once(monkeypatch):
    transaction = {"state": "synthetic-state", "nonce": "synthetic-nonce"}
    consume_transaction = Mock(side_effect=[transaction, None])
    exchange = Mock(return_value=_token_response(_successful_exchange_body()))
    create_user = Mock(return_value=SimpleNamespace(id="synthetic-user-id"))
    create_session = Mock(return_value="synthetic-session-id")
    monkeypatch.setattr(auth, "consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(auth, "urlopen", exchange)
    monkeypatch.setattr(auth, "verify_google_id_token", Mock(return_value={"sub": "synthetic-sub"}))
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)

    with TestClient(main.app, follow_redirects=False) as client:
        first_response = _callback(client)
        replay_response = _callback(client)

    assert first_response.status_code == 307
    assert replay_response.status_code == 400
    consume_transaction.assert_has_calls([call("synthetic-transaction-id")] * 2)
    exchange.assert_called_once()
    create_user.assert_called_once()
    create_session.assert_called_once()


@pytest.mark.parametrize(("secure_setting", "secure_expected"), [("false", False), ("true", True)])
def test_google_login_stores_transaction_and_redirects_state_and_nonce(
    monkeypatch, secure_setting, secure_expected
):
    transaction = {
        "transaction_id": "synthetic-transaction-id",
        "state": "synthetic-state-value",
        "nonce": "synthetic-nonce-value",
    }
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "synthetic-client-id")
    monkeypatch.setattr(auth, "GOOGLE_REDIRECT_URI", "https://example.test/callback")
    monkeypatch.setattr(auth, "create_oauth_transaction", Mock(return_value=transaction))
    monkeypatch.setattr(
        session_cookie,
        "SESSION_COOKIE_SECURE",
        secure_setting.lower() in {"true", "1", "yes", "on"},
    )

    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/auth/google")

    assert response.status_code == 307
    authorization_query = parse_qs(urlsplit(response.headers["location"]).query)
    assert authorization_query["state"] == [transaction["state"]]
    assert authorization_query["nonce"] == [transaction["nonce"]]
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    transaction_cookie = cookie["oauth_transaction"]
    assert transaction_cookie.value == transaction["transaction_id"]
    assert transaction_cookie["httponly"]
    assert transaction_cookie["samesite"] == "lax"
    assert transaction_cookie["path"] == "/"
    assert transaction_cookie["max-age"] == "600"
    assert bool(transaction_cookie["secure"]) is secure_expected
    assert transaction["state"] not in transaction_cookie.value
    assert transaction["nonce"] not in transaction_cookie.value
    assert transaction["transaction_id"] not in response.headers["location"]


def test_oauth_transaction_redis_value_has_ttl_and_is_single_use(monkeypatch):
    class InMemoryRedis:
        def __init__(self):
            self.values = {}
            self.expirations = {}

        def set(self, key, value, ex):
            self.values[key] = value
            self.expirations[key] = ex

        def getdel(self, key):
            self.expirations.pop(key, None)
            return self.values.pop(key, None)

    fake_redis = InMemoryRedis()
    monkeypatch.setattr(oauth_state, "_redis", fake_redis)

    created = oauth_state.create_oauth_transaction()
    key = f"oauth_transaction:{created['transaction_id']}"
    stored_payload = fake_redis.values[key]
    assert fake_redis.expirations[key] == 10 * 60
    assert created["state"] in stored_payload
    assert created["nonce"] in stored_payload

    consumed = oauth_state.consume_oauth_transaction(created["transaction_id"])
    assert consumed == {"state": created["state"], "nonce": created["nonce"]}
    assert oauth_state.consume_oauth_transaction(created["transaction_id"]) is None


def test_invalid_id_token_prevents_user_and_session_creation(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(return_value=_token_response(_successful_exchange_body())))
    monkeypatch.setattr(
        auth,
        "verify_google_id_token",
        Mock(side_effect=ValueError("synthetic invalid token")),
    )
    create_user = Mock()
    create_session = Mock()
    set_cookie = Mock()
    monkeypatch.setattr(auth, "get_or_create_google_user", create_user)
    monkeypatch.setattr(auth, "create_session", create_session)
    monkeypatch.setattr(auth, "set_session_cookie", set_cookie)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 400
    assert response.json() == {"detail": "Google ID token is invalid."}
    create_user.assert_not_called()
    create_session.assert_not_called()
    set_cookie.assert_not_called()


def test_user_creation_failure_does_not_set_cookie(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(return_value=_token_response(_successful_exchange_body())))
    monkeypatch.setattr(auth, "verify_google_id_token", Mock(return_value={"sub": "synthetic-sub"}))
    monkeypatch.setattr(
        auth,
        "get_or_create_google_user",
        Mock(side_effect=RuntimeError("synthetic database failure")),
    )
    create_session = Mock()
    set_cookie = Mock()
    monkeypatch.setattr(auth, "create_session", create_session)
    monkeypatch.setattr(auth, "set_session_cookie", set_cookie)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not complete login."}
    assert "synthetic database failure" not in response.text
    create_session.assert_not_called()
    set_cookie.assert_not_called()
    assert "set-cookie" not in response.headers


def test_session_creation_failure_does_not_set_cookie(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(return_value=_token_response(_successful_exchange_body())))
    monkeypatch.setattr(auth, "verify_google_id_token", Mock(return_value={"sub": "synthetic-sub"}))
    monkeypatch.setattr(
        auth,
        "get_or_create_google_user",
        Mock(return_value=SimpleNamespace(id="synthetic-user-id")),
    )
    monkeypatch.setattr(
        auth,
        "create_session",
        Mock(side_effect=RuntimeError("synthetic Redis failure")),
    )
    set_cookie = Mock()
    monkeypatch.setattr(auth, "set_session_cookie", set_cookie)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not complete login."}
    assert "synthetic Redis failure" not in response.text
    set_cookie.assert_not_called()
    assert "set-cookie" not in response.headers


def test_cookie_failure_deletes_created_redis_session(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(return_value=_token_response(_successful_exchange_body())))
    monkeypatch.setattr(auth, "verify_google_id_token", Mock(return_value={"sub": "synthetic-sub"}))
    monkeypatch.setattr(
        auth,
        "get_or_create_google_user",
        Mock(return_value=SimpleNamespace(id="synthetic-user-id")),
    )
    monkeypatch.setattr(auth, "create_session", Mock(return_value="synthetic-session-id"))
    monkeypatch.setattr(
        auth,
        "set_session_cookie",
        Mock(side_effect=RuntimeError("synthetic cookie failure")),
    )
    delete_session = Mock()
    monkeypatch.setattr(auth, "delete_session", delete_session)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not complete login."}
    delete_session.assert_called_once_with("synthetic-session-id")
    assert "set-cookie" not in response.headers


def test_google_rejection_returns_generic_error(monkeypatch):
    raw_google_body = b'{"error":"invalid_grant","error_description":"synthetic raw body"}'
    token_request = Mock(
        side_effect=HTTPError(
            "https://oauth2.googleapis.com/token",
            400,
            "Bad Request",
            {},
            io.BytesIO(raw_google_body),
        )
    )
    monkeypatch.setattr(auth, "urlopen", token_request)

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client, code="synthetic-invalid-code")

    assert response.status_code == 400
    assert response.json() == {"detail": "Google token exchange failed."}
    assert "synthetic raw body" not in response.text


def test_google_token_network_failure_returns_generic_502(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(side_effect=TimeoutError()))

    with TestClient(main.app, follow_redirects=False) as client:
        response = _callback(client)

    assert response.status_code == 502
    assert response.json() == {"detail": "Google token service is unavailable."}
    assert "synthetic" not in response.text
