from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from backend.app.config import FRONTEND_URL


def validate_frontend_origin(request: Request) -> None:
    """Reject browser requests whose Origin is not the configured frontend."""
    origin = request.headers.get("origin")
    if origin is None:
        # Compatibility for non-browser clients; this leaves a CSRF limitation.
        return

    frontend = urlsplit(FRONTEND_URL)
    if frontend.scheme not in {"http", "https"} or not frontend.netloc:
        raise HTTPException(status_code=500, detail="Origin validation is unavailable.")

    allowed_origin = f"{frontend.scheme}://{frontend.netloc}"
    if origin != allowed_origin:
        raise HTTPException(status_code=403, detail="Origin not allowed.")
