from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from backend.app import main, transcription
from backend.app.auth import get_current_user
from backend.app.database import get_db


WEBM = b"\x1a\x45\xdf\xa3" + b"synthetic audio"


@pytest.fixture(autouse=True)
def quota_stub(monkeypatch):
    monkeypatch.setattr(main, "reserve_voice", Mock())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: Mock())
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: Mock())
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize("text", ["What is the policy?", "విధానం ఏమిటి?", "नीति क्या है?"])
def test_transcription_preserves_text_and_detected_language(monkeypatch, text):
    create = Mock(return_value=SimpleNamespace(
        text=f" {text} ", model_dump=lambda: {"languages": [{"code": "te"}]},
    ))
    monkeypatch.setattr(transcription.client.audio.transcriptions, "create", create)
    assert transcription.transcribe_audio(WEBM, "webm") == {"text": text, "languages": ["te"]}
    assert create.call_args.kwargs == {"model": "gpt-transcribe", "file": ("recording.webm", WEBM)}


def test_missing_metadata_is_not_invented(monkeypatch):
    monkeypatch.setattr(transcription.client.audio.transcriptions, "create", Mock(
        return_value=SimpleNamespace(text="Hello", model_dump=lambda: {}),
    ))
    assert transcription.transcribe_audio(WEBM, "webm")["languages"] is None


def test_empty_provider_transcript(monkeypatch):
    monkeypatch.setattr(transcription.client.audio.transcriptions, "create", Mock(
        return_value=SimpleNamespace(text="  "),
    ))
    with pytest.raises(ValueError):
        transcription.transcribe_audio(WEBM, "webm")


def test_endpoint_success(client, monkeypatch):
    service = Mock(return_value={"text": "విధానం ఏమిటి?", "languages": ["te"]})
    monkeypatch.setattr(main, "transcribe_audio", service)
    response = client.post("/transcribe", files={"file": ("recording.webm", WEBM, "audio/webm")})
    assert response.status_code == 200
    assert response.json() == service.return_value
    service.assert_called_once_with(WEBM, "webm")


@pytest.mark.parametrize("filename,data,status", [
    ("recording.webm", b"", 400),
    ("recording.webm", b"this is text", 415),
    ("recording.exe", WEBM, 415),
    ("recording.webm", WEBM * 10, 413),
])
def test_invalid_uploads_skip_provider(client, monkeypatch, filename, data, status):
    monkeypatch.setattr(main, "MAX_AUDIO_BYTES", 100)
    service = Mock()
    monkeypatch.setattr(main, "transcribe_audio", service)
    response = client.post("/transcribe", files={"file": (filename, data)})
    assert response.status_code == status
    service.assert_not_called()


def test_missing_file(client):
    assert client.post("/transcribe").status_code == 422


@pytest.mark.parametrize("headers", [{"origin": "https://untrusted.test"}, {"sec-fetch-site": "cross-site"}])
def test_security_protections(client, headers):
    assert client.post("/transcribe", headers=headers, files={"file": ("recording.webm", WEBM)}).status_code == 403


def test_authentication_required(client, monkeypatch):
    monkeypatch.delitem(main.app.dependency_overrides, get_current_user)
    assert client.post("/transcribe", files={"file": ("recording.webm", WEBM)}).status_code == 401


@pytest.mark.parametrize("error,status", [(ValueError("empty"), 422), (RuntimeError("secret-provider-details"), 502)])
def test_provider_failures_close_upload(monkeypatch, error, status):
    monkeypatch.setattr(main, "transcribe_audio", Mock(side_effect=error))
    audio = BytesIO(WEBM)
    with pytest.raises(HTTPException) as caught:
        main.transcribe(file=UploadFile(filename="recording.webm", file=audio), current_user=Mock())
    assert caught.value.status_code == status
    assert "secret-provider-details" not in caught.value.detail
    assert audio.closed
