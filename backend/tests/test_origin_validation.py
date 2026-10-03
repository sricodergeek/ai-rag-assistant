import pytest
from fastapi.testclient import TestClient

from backend.app import main, origin_validation


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        origin_validation,
        "FRONTEND_URL",
        "https://frontend.example.test/app",
    )
    with TestClient(main.app) as test_client:
        yield test_client


def _request(
    client,
    endpoint: str,
    origin: str | None = None,
    fetch_site: str | None = None,
):
    headers = {"Origin": origin} if origin is not None else {}
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    if endpoint == "upload":
        return client.post(
            "/upload",
            headers=headers,
            files={"file": ("synthetic.pdf", b"synthetic bytes", "application/pdf")},
        )
    if endpoint == "ask":
        return client.post(
            "/ask",
            headers=headers,
            json={
                "question": "Synthetic question",
                "conversation_id": "00000000-0000-0000-0000-000000000001",
                "document_id": "00000000-0000-0000-0000-000000000002",
            },
        )
    if endpoint == "delete":
        return client.delete(
            "/documents/00000000-0000-0000-0000-000000000002",
            headers=headers,
        )
    return client.post("/auth/logout", headers=headers)


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
def test_configured_origin_passes_validation_and_existing_auth_behavior_remains(
    client, endpoint
):
    response = _request(client, endpoint, "https://frontend.example.test")

    if endpoint == "logout":
        assert response.status_code == 200
    else:
        # Origin validation allows the request to reach the existing auth dependency.
        assert response.status_code == 401


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
def test_disallowed_origin_is_rejected_on_each_protected_endpoint(client, endpoint):
    response = _request(
        client,
        endpoint,
        "https://attacker.example.test",
        fetch_site="same-origin",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Origin not allowed."}


@pytest.mark.parametrize(
    "origin",
    [
        "https://frontend.example.test.attacker.example",
        "https://frontend.example.test:444",
        "http://frontend.example.test",
        "null",
    ],
)
def test_similar_but_different_origin_is_rejected(client, origin):
    response = _request(client, "logout", origin)

    assert response.status_code == 403


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
def test_missing_origin_is_allowed_for_compatibility(client, endpoint):
    response = _request(client, endpoint)

    if endpoint == "logout":
        assert response.status_code == 200
    else:
        assert response.status_code == 401


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
@pytest.mark.parametrize("fetch_site", ["same-origin", "same-site", "none"])
def test_allowed_fetch_site_values_reach_existing_endpoint_behavior(
    client, endpoint, fetch_site
):
    response = _request(
        client,
        endpoint,
        "https://frontend.example.test",
        fetch_site=fetch_site,
    )

    if endpoint == "logout":
        assert response.status_code == 200
    else:
        assert response.status_code == 401


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
def test_cross_site_fetch_metadata_is_rejected_on_each_endpoint(client, endpoint):
    response = _request(
        client,
        endpoint,
        "https://frontend.example.test",
        fetch_site="cross-site",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Request site not allowed."}


@pytest.mark.parametrize("endpoint", ["upload", "ask", "logout", "delete"])
def test_unknown_fetch_metadata_value_is_rejected_on_each_endpoint(client, endpoint):
    response = _request(
        client,
        endpoint,
        "https://frontend.example.test",
        fetch_site="unexpected-context",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Request site not allowed."}
