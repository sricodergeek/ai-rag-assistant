import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.app.config import (
    FRONTEND_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
)
from backend.app.database import get_db
from backend.app.fetch_metadata import validate_fetch_metadata
from backend.app.google_identity import verify_google_id_token
from backend.app.oauth_state import (
    consume_oauth_transaction,
    create_oauth_transaction,
)
from backend.app.session_cookie import (
    clear_oauth_transaction_cookie,
    clear_session_cookie,
    set_oauth_transaction_cookie,
    set_session_cookie,
)
from backend.app.session_service import (
    create_session,
    delete_session,
    get_user_id_from_session,
)
from backend.app.models import User
from backend.app.origin_validation import validate_frontend_origin
from backend.app.user_service import get_or_create_google_user


router = APIRouter()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        user_id = get_user_id_from_session(session_id)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable.",
        ) from error

    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        parsed_user_id = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=401, detail="Authentication required.") from error

    try:
        user = db.get(User, parsed_user_id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not authenticate request.",
        ) from error

    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    return user


@router.get("/auth/google")
def google_login():
    transaction = create_oauth_transaction()
    authorization_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": transaction["state"],
        "nonce": transaction["nonce"],
    }
    authorization_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"{urlencode(authorization_params)}"
    )
    response = RedirectResponse(url=authorization_url)
    set_oauth_transaction_cookie(response, transaction["transaction_id"])
    return response


@router.get("/auth/me")
def auth_me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
    }


@router.post("/auth/logout")
def logout(
    request: Request,
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
):
    session_id = request.cookies.get("session_id")
    if session_id:
        try:
            delete_session(session_id)
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail="Could not complete logout.",
            ) from error

    response = JSONResponse({"message": "Logged out successfully."})
    clear_session_cookie(response)
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    transaction_id = request.cookies.get("oauth_transaction")
    if not transaction_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth transaction.",
        )

    try:
        transaction = consume_oauth_transaction(transaction_id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not complete login.",
        ) from error

    if transaction is None or transaction.get("state") != state:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth transaction.",
        )

    if error is not None:
        response = RedirectResponse(url=FRONTEND_URL)
        clear_oauth_transaction_cookie(response)
        return response

    if code is None:
        raise HTTPException(status_code=400, detail="OAuth authorization failed.")

    expected_nonce = transaction["nonce"]

    token_request = UrlRequest(
        "https://oauth2.googleapis.com/token",
        data=urlencode(
            {
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(token_request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise HTTPException(
                    status_code=502,
                    detail="Google token exchange failed.",
                )
            google_token_response = json.loads(response.read())
    except HTTPError as error:
        status_code = 400 if 400 <= error.code < 500 else 502
        raise HTTPException(
            status_code=status_code,
            detail="Google token exchange failed.",
        ) from error
    except (URLError, TimeoutError) as error:
        raise HTTPException(
            status_code=502,
            detail="Google token service is unavailable.",
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=502,
            detail="Google token exchange failed.",
        ) from error

    google_id_token = google_token_response.get("id_token")
    if not isinstance(google_id_token, str) or not google_id_token:
        raise HTTPException(
            status_code=502,
            detail="Google token exchange failed.",
        )

    try:
        identity = verify_google_id_token(google_id_token, expected_nonce)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Google ID token is invalid.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Google identity verification is unavailable.",
        ) from error

    try:
        user = get_or_create_google_user(db, identity)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not complete login.",
        ) from error

    try:
        session_id = create_session(user.id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not complete login.",
        ) from error

    try:
        redirect_response = RedirectResponse(url=FRONTEND_URL)
        clear_oauth_transaction_cookie(redirect_response)
        set_session_cookie(redirect_response, session_id)
    except Exception as error:
        try:
            delete_session(session_id)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail="Could not complete login.",
        ) from error

    return redirect_response
