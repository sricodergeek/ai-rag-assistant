from fastapi import HTTPException, Request


_ALLOWED_FETCH_SITES = {"same-origin", "same-site", "none"}


def validate_fetch_metadata(request: Request) -> None:
    """Reject state-changing browser requests marked as cross-site."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is None:
        # Compatibility for clients that do not send Fetch Metadata.
        return

    if fetch_site not in _ALLOWED_FETCH_SITES:
        raise HTTPException(status_code=403, detail="Request site not allowed.")
