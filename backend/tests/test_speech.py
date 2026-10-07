from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app import main, speech, voice_usage
from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.tests.test_voice_usage import counters
from backend.tests.test_ask import StubSession, _use_session
from backend.app.models import Document


@pytest.fixture
def client(monkeypatch, counters):
    user = SimpleNamespace(id=uuid.uuid4())
    db = Mock()
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: user)
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: db)
    monkeypatch.setattr(main, "generate_speech", Mock(return_value=(b"MP3-audio", "audio/mpeg")))
    monkeypatch.setattr(main, "transcribe_audio", Mock(return_value={"text": "Hello", "languages": ["en"]}))
    with TestClient(main.app) as client:
        yield client, user, db


def request_feature(client, feature):
    if feature == "tts":
        return client.post("/speak", json={"text": "नीति क्या है?"})
    return client.post("/transcribe", files={"file": ("voice.webm", b"\x1a\x45\xdf\xa3audio", "audio/webm")})


@pytest.mark.parametrize("feature", ["stt", "tts"])
def test_direct_requests_cannot_bypass_limit(client, feature):
    http, user, _ = client
    assert request_feature(http, feature).status_code == 200
    assert request_feature(http, feature).status_code == 200
    response = request_feature(http, feature)
    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": "voice_quota_exhausted", "feature": feature, "limit": 2,
        "used": 2, "remaining": 0, "resets_at": "2026-10-07T00:00:00+00:00",
    }
    provider = main.generate_speech if feature == "tts" else main.transcribe_audio
    assert provider.call_count == 2
    user.id = uuid.uuid4()
    assert request_feature(http, feature).status_code == 200


def test_binary_response_and_unchanged_text(client):
    http, _, _ = client
    text = "  తెలుగు సమాధానం. [Source: example.pdf, Page 1]  "
    response = http.post("/speak", json={"text": text})
    assert response.status_code == 200
    assert response.content == b"MP3-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "no-store"
    main.generate_speech.assert_called_once_with(text)


@pytest.mark.parametrize("text,status", [("", 422), ("  ", 422), ("x" * 4096, 200), ("x" * 4097, 422)])
def test_input_boundaries_do_not_charge_invalid_requests(client, text, status):
    http, _, _ = client
    assert http.post("/speak", json={"text": text}).status_code == status
    assert http.get("/voice/usage").json()["tts"]["used"] == (1 if status == 200 else 0)


@pytest.mark.parametrize("feature", ["stt", "tts"])
def test_provider_failure_consumes_reserved_allowance(client, monkeypatch, feature):
    http, _, _ = client
    provider = "generate_speech" if feature == "tts" else "transcribe_audio"
    monkeypatch.setattr(main, provider, Mock(side_effect=RuntimeError("secret-credit-error")))
    response = request_feature(http, feature)
    assert response.status_code == 502
    assert "secret-credit-error" not in response.text
    assert http.get("/voice/usage").json()[feature]["used"] == 1


@pytest.mark.parametrize("feature", ["stt", "tts"])
def test_redis_failure_blocks_provider(client, monkeypatch, feature):
    http, _, _ = client
    monkeypatch.setattr(voice_usage._redis, "eval", Mock(side_effect=RuntimeError("redis-secret")))
    response = request_feature(http, feature)
    assert response.status_code == 503
    assert "redis-secret" not in response.text
    (main.generate_speech if feature == "tts" else main.transcribe_audio).assert_not_called()


@pytest.mark.parametrize("document", [None, SimpleNamespace(user_id=uuid.uuid4())])
def test_ownership_rejected_before_quota(client, document):
    http, _, db = client
    db.get.return_value = document
    response = http.post("/speak", json={"text": "Hello", "document_id": str(uuid.uuid4())})
    assert response.status_code == 404
    assert http.get("/voice/usage").json()["tts"]["used"] == 0
    main.generate_speech.assert_not_called()


def test_owned_document_accepted(client):
    http, user, db = client
    db.get.return_value = SimpleNamespace(user_id=user.id)
    assert http.post("/speak", json={"text": "Hello", "document_id": str(uuid.uuid4())}).status_code == 200


def test_invalid_audio_does_not_consume_quota(client):
    http, _, _ = client
    assert http.post("/transcribe", files={"file": ("voice.webm", b"invalid")}).status_code == 415
    assert http.get("/voice/usage").json()["stt"]["used"] == 0


def test_usage_structure_and_failure(client, monkeypatch):
    http, _, _ = client
    response = http.get("/voice/usage")
    assert response.status_code == 200
    assert response.json() == {feature: {"limit": 2, "used": 0, "remaining": 2, "resets_at": "2026-10-07T00:00:00+00:00"} for feature in ("stt", "tts")}
    monkeypatch.setattr(voice_usage._redis, "mget", Mock(side_effect=RuntimeError("secret")))
    assert http.get("/voice/usage").status_code == 503


@pytest.mark.parametrize("path", ["/voice/usage", "/speak", "/transcribe"])
def test_authentication_required(client, monkeypatch, path):
    http, _, _ = client
    monkeypatch.delitem(main.app.dependency_overrides, get_current_user)
    response = http.get(path) if path == "/voice/usage" else (request_feature(http, "tts" if path == "/speak" else "stt"))
    assert response.status_code == 401


@pytest.mark.parametrize("headers", [{"origin": "https://untrusted.test"}, {"sec-fetch-site": "cross-site"}])
def test_speech_origin_protections(client, headers):
    http, _, _ = client
    assert http.post("/speak", json={"text": "Hello"}, headers=headers).status_code == 403
    main.generate_speech.assert_not_called()


@pytest.mark.parametrize("feature", ["stt", "tts"])
def test_ask_is_available_after_voice_exhaustion(client, monkeypatch, feature):
    http, user, _ = client
    for _ in range(2):
        assert request_feature(http, feature).status_code == 200
    assert request_feature(http, feature).status_code == 429
    document = Document(id=uuid.uuid4(), user_id=user.id, filename="example.pdf")
    session = StubSession(document)
    _use_session(monkeypatch, session, user)
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[0.1]))
    monkeypatch.setattr(main, "search_documents", Mock(return_value=[]))
    assert http.post("/ask", json={"question": "What is the policy?", "document_id": str(document.id), "conversation_id": str(uuid.uuid4())}).status_code == 200


@pytest.mark.parametrize("text", ["English answer", "हिंदी उत्तर", "తెలుగు సమాధానం"])
def test_speech_service_preserves_language_and_configuration(monkeypatch, text):
    response = Mock()
    response.read.return_value = b"audio"
    manager = Mock()
    manager.__enter__ = Mock(return_value=response)
    manager.__exit__ = Mock(return_value=False)
    create = Mock(return_value=manager)
    monkeypatch.setattr(speech.client.audio.speech.with_streaming_response, "create", create)
    assert speech.generate_speech(text) == (b"audio", "audio/mpeg")
    create.assert_called_once_with(model=speech.TTS_MODEL, voice=speech.TTS_VOICE, input=text, response_format=speech.TTS_FORMAT)
