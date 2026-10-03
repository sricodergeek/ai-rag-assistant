from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from backend.app import config, main


def _configured_frontend_origin() -> str:
    frontend = urlsplit(config.FRONTEND_URL)
    return f"{frontend.scheme}://{frontend.netloc}"


def test_allowed_frontend_origin_receives_credentialed_cors_headers():
    origin = _configured_frontend_origin()

    with TestClient(main.app) as client:
        response = client.get("/auth/me", headers={"Origin": origin})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] != "*"


def test_allowed_credentialed_preflight_succeeds():
    origin = _configured_frontend_origin()

    with TestClient(main.app) as client:
        response = client.options(
            "/ask",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["access-control-allow-origin"] != "*"


def test_delete_is_allowed_for_configured_frontend_origin():
    origin = _configured_frontend_origin()

    with TestClient(main.app) as client:
        response = client.options(
            "/documents/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "DELETE",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "DELETE" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] != "*"


def test_disallowed_origin_gets_no_credentialed_cors_permission():
    origin = "https://attacker.example.test"

    with TestClient(main.app) as client:
        response = client.options(
            "/ask",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") is None
    assert response.headers.get("access-control-allow-origin") != origin
    assert response.headers.get("access-control-allow-origin") != "*"
