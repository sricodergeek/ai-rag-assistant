import time
from unittest.mock import Mock

import pytest

from backend.app import google_identity


def _valid_claims():
    return {
        "iss": "https://accounts.google.com",
        "aud": "synthetic-google-client-id",
        "exp": int(time.time()) + 300,
        "nonce": "synthetic-oidc-nonce",
        "sub": "synthetic-google-subject",
        "email": "synthetic@example.test",
        "name": "Synthetic User",
        "picture": "https://example.test/synthetic-avatar.png",
    }


def test_valid_verified_token_returns_only_identity_fields(monkeypatch):
    token = "synthetic.raw.id-token"
    claims = {
        **_valid_claims(),
        "access_token": "synthetic-access-token",
        "refresh_token": "synthetic-refresh-token",
    }
    verify = Mock(return_value=claims)
    monkeypatch.setattr(google_identity, "GOOGLE_CLIENT_ID", "synthetic-google-client-id")
    monkeypatch.setattr(google_identity.id_token, "verify_oauth2_token", verify)

    identity = google_identity.verify_google_id_token(token, "synthetic-oidc-nonce")

    assert identity == {
        "sub": "synthetic-google-subject",
        "email": "synthetic@example.test",
        "name": "Synthetic User",
        "picture": "https://example.test/synthetic-avatar.png",
    }
    assert token not in repr(identity)
    assert "synthetic-access-token" not in repr(identity)
    assert "synthetic-refresh-token" not in repr(identity)
    verify.assert_called_once()
    assert verify.call_args.args[0] == token
    assert verify.call_args.kwargs["audience"] == "synthetic-google-client-id"


def test_invalid_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        google_identity.id_token,
        "verify_oauth2_token",
        Mock(side_effect=ValueError("invalid synthetic token")),
    )

    with pytest.raises(ValueError):
        google_identity.verify_google_id_token(
            "synthetic-invalid-token", "synthetic-oidc-nonce"
        )


def test_wrong_audience_is_rejected(monkeypatch):
    claims = _valid_claims()
    claims["aud"] = "some-other-client-id"
    monkeypatch.setattr(google_identity, "GOOGLE_CLIENT_ID", "synthetic-google-client-id")
    monkeypatch.setattr(
        google_identity.id_token,
        "verify_oauth2_token",
        Mock(return_value=claims),
    )

    with pytest.raises(ValueError, match="audience"):
        google_identity.verify_google_id_token(
            "synthetic-wrong-audience-token", "synthetic-oidc-nonce"
        )


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        google_identity.id_token,
        "verify_oauth2_token",
        Mock(side_effect=ValueError("synthetic token expired")),
    )

    with pytest.raises(ValueError, match="expired"):
        google_identity.verify_google_id_token(
            "synthetic-expired-token", "synthetic-oidc-nonce"
        )


def test_untrusted_issuer_is_rejected(monkeypatch):
    claims = _valid_claims()
    claims["iss"] = "https://attacker.example.test"
    monkeypatch.setattr(google_identity, "GOOGLE_CLIENT_ID", "synthetic-google-client-id")
    monkeypatch.setattr(
        google_identity.id_token,
        "verify_oauth2_token",
        Mock(return_value=claims),
    )

    with pytest.raises(ValueError, match="issuer"):
        google_identity.verify_google_id_token(
            "synthetic-wrong-issuer-token", "synthetic-oidc-nonce"
        )


def test_wrong_nonce_is_rejected(monkeypatch):
    monkeypatch.setattr(
        google_identity,
        "GOOGLE_CLIENT_ID",
        "synthetic-google-client-id",
    )
    monkeypatch.setattr(
        google_identity.id_token,
        "verify_oauth2_token",
        Mock(return_value=_valid_claims()),
    )

    with pytest.raises(ValueError, match="nonce"):
        google_identity.verify_google_id_token(
            "synthetic-wrong-nonce-token", "different-synthetic-nonce"
        )
