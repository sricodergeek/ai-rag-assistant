import uuid
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.database import SessionLocal, get_db
from backend.app.auth import get_current_user
from backend.app.models import Document, User


def _install_database_override(session):
    def override_get_db():
        yield session

    main.app.dependency_overrides[get_db] = override_get_db


def _create_synthetic_user(session):
    user = User(
        email=f"upload-{uuid.uuid4()}@example.test",
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )
    session.add(user)
    session.commit()
    return user


def _install_authenticated_user_override(user):
    def override_current_user():
        return user

    main.app.dependency_overrides[get_current_user] = override_current_user


def test_upload_creates_document_and_reuses_its_id_for_ingestion(monkeypatch):
    session = SessionLocal()
    user = _create_synthetic_user(session)
    uploaded_content = b"synthetic upload bytes; PDF ingestion is stubbed"
    ingested = {}

    def stub_ingest_pdf(file_path: str, document_id: str):
        assert Path(file_path).name == "synthetic-eval.pdf"
        assert Path(file_path).read_bytes() == uploaded_content
        ingested["document_id"] = document_id
        return 4, document_id

    monkeypatch.setattr(main, "ingest_pdf", stub_ingest_pdf)
    _install_database_override(session)
    _install_authenticated_user_override(user)

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/upload",
                headers={"Origin": "http://localhost:5173"},
                files={
                    "file": (
                        "synthetic-eval.pdf",
                        uploaded_content,
                        "application/pdf",
                    )
                },
            )

        assert response.status_code == 200
        payload = response.json()
        document_id = uuid.UUID(payload["document_id"])
        document = session.get(Document, document_id)

        assert document is not None
        assert document.filename == "synthetic-eval.pdf"
        assert document.user_id == user.id
        assert payload["document_id"] == str(document.id)
        assert ingested["document_id"] == str(document.id)
        assert payload["chunks_ingested"] == 4
        assert (
            session.query(Document)
            .filter(Document.id == document_id)
            .count()
            == 1
        )
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(get_current_user, None)
        document_id_value = ingested.get("document_id")
        if document_id_value:
            document = session.get(Document, uuid.UUID(document_id_value))
            if document is not None:
                session.delete(document)
                session.commit()
        session.delete(user)
        session.commit()
        session.close()


def test_upload_removes_document_record_when_ingestion_fails(monkeypatch):
    session = SessionLocal()
    user = _create_synthetic_user(session)
    ingested = {}

    def failing_ingest_pdf(file_path: str, document_id: str):
        ingested["document_id"] = document_id
        raise RuntimeError("stubbed ingestion failure")

    monkeypatch.setattr(main, "ingest_pdf", failing_ingest_pdf)
    _install_database_override(session)
    _install_authenticated_user_override(user)

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/upload",
                files={
                    "file": (
                        "synthetic-failure.pdf",
                        b"synthetic bytes; no PDF reader is called",
                        "application/pdf",
                    )
                },
            )

        assert response.status_code == 500
        document_id = uuid.UUID(ingested["document_id"])
        assert session.get(Document, document_id) is None
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(get_current_user, None)
        session.delete(user)
        session.commit()
        session.close()


def test_unauthenticated_upload_does_not_create_document_or_ingest(monkeypatch):
    session = SessionLocal()
    baseline_document_count = session.query(Document).count()
    ingest_pdf = Mock()
    monkeypatch.setattr(main, "ingest_pdf", ingest_pdf)
    _install_database_override(session)

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/upload",
                files={
                    "file": (
                        "synthetic-unauthenticated.pdf",
                        b"synthetic upload bytes; ingestion must not run",
                        "application/pdf",
                    )
                },
            )

        assert response.status_code == 401
        assert session.query(Document).count() == baseline_document_count
        assert ingest_pdf.call_count == 0
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        session.close()
