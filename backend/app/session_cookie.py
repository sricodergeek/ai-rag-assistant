from starlette.responses import Response

from backend.app.config import SESSION_COOKIE_SECURE


_COOKIE_NAME = "session_id"
_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_OAUTH_TRANSACTION_COOKIE_NAME = "oauth_transaction"
_OAUTH_TRANSACTION_COOKIE_MAX_AGE_SECONDS = 10 * 60
def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session_id,
        max_age=_COOKIE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value="",
        max_age=0,
        path="/",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )


def set_oauth_transaction_cookie(response: Response, transaction_id: str) -> None:
    response.set_cookie(
        key=_OAUTH_TRANSACTION_COOKIE_NAME,
        value=transaction_id,
        max_age=_OAUTH_TRANSACTION_COOKIE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )


def clear_oauth_transaction_cookie(response: Response) -> None:
    response.set_cookie(
        key=_OAUTH_TRANSACTION_COOKIE_NAME,
        value="",
        max_age=0,
        path="/",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )
