from google.auth.transport.requests import Request
from google.oauth2 import id_token

from backend.app.config import GOOGLE_CLIENT_ID


_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_IDENTITY_FIELDS = ("sub", "email", "name", "picture")


def verify_google_id_token(id_token_value: str, expected_nonce: str) -> dict:
    """Cryptographically verify a Google ID token and return identity claims only."""
    claims = id_token.verify_oauth2_token(
        id_token_value,
        Request(),
        audience=GOOGLE_CLIENT_ID,
    )

    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise ValueError("Invalid Google ID token issuer.")

    audience = claims.get("aud")
    if not (
        audience == GOOGLE_CLIENT_ID
        or isinstance(audience, list)
        and GOOGLE_CLIENT_ID in audience
    ):
        raise ValueError("Invalid Google ID token audience.")

    if claims.get("nonce") != expected_nonce:
        raise ValueError("Invalid Google ID token nonce.")

    return {
        field: claims[field]
        for field in _IDENTITY_FIELDS
        if field in claims
    }
