import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import main
from backend.app.auth import get_current_user
from backend.app.database import SessionLocal, get_db
from backend.app.models import Conversation, Document, Message, User
from backend.app import vector_store


def _new_user(prefix: str) -> User:
    return User(
        email=f"{prefix}-{uuid.uuid4()}@example.test",
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )


@pytest.fixture
def seeded_documents():
    db = SessionLocal()
    owner = _new_user("documents-owner")
    other_user = _new_user("documents-other")
    older_id, tied_id_low, tied_id_high, other_id = [uuid.uuid4() for _ in range(4)]
    created_at = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    older_at = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    documents = [
        Document(
            id=older_id,
            user=owner,
            filename="owner-older.pdf",
            created_at=older_at,
        ),
        Document(
            id=tied_id_low,
            user=owner,
            filename="owner-tie-lower-id.pdf",
            created_at=created_at,
        ),
        Document(
            id=tied_id_high,
            user=owner,
            filename="owner-tie-higher-id.pdf",
            created_at=created_at,
        ),
        Document(
            id=other_id,
            user=other_user,
            filename="other-user.pdf",
            created_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        ),
    ]
    db.add_all([owner, other_user, *documents])
    db.commit()
    try:
        yield db, owner, other_user, documents
    finally:
        db.rollback()
        conversation_ids = db.scalars(
            select(Conversation.id).where(
                Conversation.document_id.in_([document.id for document in documents])
            )
        ).all()
        if conversation_ids:
            db.query(Message).filter(
                Message.conversation_id.in_(conversation_ids)
            ).delete(synchronize_session=False)
            db.query(Conversation).filter(
                Conversation.id.in_(conversation_ids)
            ).delete(synchronize_session=False)
        db.query(Document).filter(
            Document.id.in_([document.id for document in documents])
        ).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([owner.id, other_user.id])).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


def _override_db(monkeypatch, db):
    def override_get_db():
        yield db

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_get_db)


def _override_user(monkeypatch, user):
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user,
        lambda: user,
    )


def test_documents_requires_authentication(monkeypatch, seeded_documents):
    db, _, _, _ = seeded_documents
    _override_db(monkeypatch, db)

    with TestClient(main.app) as client:
        response = client.get("/documents")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


def test_documents_returns_only_current_users_safe_fields_in_newest_stable_order(
    monkeypatch, seeded_documents
):
    db, owner, other_user, documents = seeded_documents
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)
    tied_documents = sorted(documents[1:3], key=lambda document: document.id, reverse=True)

    with TestClient(main.app) as client:
        response = client.get(f"/documents?user_id={other_user.id}")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [
        str(tied_documents[0].id),
        str(tied_documents[1].id),
        str(documents[0].id),
    ]
    assert {item["filename"] for item in payload} == {
        "owner-older.pdf",
        "owner-tie-lower-id.pdf",
        "owner-tie-higher-id.pdf",
    }
    assert all(set(item) == {"id", "filename", "created_at"} for item in payload)
    assert all("other-user.pdf" != item["filename"] for item in payload)
    assert all(str(other_user.id) not in str(item) for item in payload)


def test_documents_returns_empty_array_when_user_has_none(monkeypatch):
    db = SessionLocal()
    owner = _new_user("documents-empty")
    db.add(owner)
    db.commit()
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)

    try:
        with TestClient(main.app) as client:
            response = client.get("/documents")

        assert response.status_code == 200
        assert response.json() == []
    finally:
        db.rollback()
        db.delete(owner)
        db.commit()
        db.close()


def test_documents_database_error_is_generic(monkeypatch):
    internal_error = "synthetic database connection detail"
    db = Mock()
    db.scalars.side_effect = RuntimeError(internal_error)
    user = _new_user("documents-query-error")
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, user)

    with TestClient(main.app) as client:
        response = client.get("/documents")

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not load documents."}
    assert internal_error not in response.text
    db.rollback.assert_called_once_with()


def test_owner_delete_removes_document_conversations_messages_and_only_its_vectors(
    monkeypatch, seeded_documents
):
    db, owner, _, documents = seeded_documents
    target = documents[0]
    untouched_document = documents[1]
    target_conversation = Conversation(document_id=target.id)
    untouched_conversation = Conversation(document_id=untouched_document.id)
    target_message = Message(
        conversation=target_conversation,
        role="user",
        content="synthetic message",
    )
    untouched_message = Message(
        conversation=untouched_conversation,
        role="assistant",
        content="synthetic response",
    )
    db.add_all([target_conversation, untouched_conversation, target_message, untouched_message])
    db.commit()
    target_conversation_id = target_conversation.id
    untouched_conversation_id = untouched_conversation.id
    target_message_id = target_message.id
    untouched_message_id = untouched_message.id
    deleted_vector_ids = []
    monkeypatch.setattr(
        main,
        "delete_document_vectors",
        lambda document_id: deleted_vector_ids.append(document_id),
    )
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)

    with TestClient(main.app) as client:
        response = client.delete(f"/documents/{target.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert db.get(Document, target.id) is None
    assert db.get(Conversation, target_conversation_id) is None
    assert db.get(Message, target_message_id) is None
    assert db.get(Document, untouched_document.id) is not None
    assert db.get(Conversation, untouched_conversation_id) is not None
    assert db.get(Message, untouched_message_id) is not None
    assert deleted_vector_ids == [str(target.id)]


def test_delete_requires_authentication(monkeypatch, seeded_documents):
    db, _, _, documents = seeded_documents
    _override_db(monkeypatch, db)
    delete_vectors = Mock()
    monkeypatch.setattr(main, "delete_document_vectors", delete_vectors)

    with TestClient(main.app) as client:
        response = client.delete(f"/documents/{documents[0].id}")

    assert response.status_code == 401
    assert db.get(Document, documents[0].id) is not None
    delete_vectors.assert_not_called()


def test_non_owner_and_missing_document_return_the_same_generic_404(
    monkeypatch, seeded_documents
):
    db, _, other_user, documents = seeded_documents
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, other_user)
    delete_vectors = Mock()
    monkeypatch.setattr(main, "delete_document_vectors", delete_vectors)

    with TestClient(main.app) as client:
        non_owner_response = client.delete(f"/documents/{documents[0].id}")
        missing_response = client.delete(f"/documents/{uuid.uuid4()}")

    assert non_owner_response.status_code == missing_response.status_code == 404
    assert non_owner_response.json() == missing_response.json() == {
        "detail": "Document not found."
    }
    assert str(documents[0].id) not in non_owner_response.text
    delete_vectors.assert_not_called()
    assert db.get(Document, documents[0].id) is not None


def test_delete_rejects_malformed_document_uuid(monkeypatch, seeded_documents):
    db, owner, _, _ = seeded_documents
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)
    delete_vectors = Mock()
    monkeypatch.setattr(main, "delete_document_vectors", delete_vectors)

    with TestClient(main.app) as client:
        response = client.delete("/documents/not-a-uuid")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    delete_vectors.assert_not_called()


def test_chroma_failure_is_safe_and_postgres_deletion_stays_committed(
    monkeypatch, seeded_documents
):
    db, owner, _, documents = seeded_documents
    target = documents[0]
    internal_error = "synthetic Chroma backend detail"
    delete_vectors = Mock(side_effect=RuntimeError(internal_error))
    monkeypatch.setattr(main, "delete_document_vectors", delete_vectors)
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)

    with TestClient(main.app) as client:
        response = client.delete(f"/documents/{target.id}")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Document was deleted, but vector cleanup could not be completed."
    }
    assert internal_error not in response.text
    assert db.get(Document, target.id) is None
    delete_vectors.assert_called_once_with(str(target.id))


def test_postgres_failure_rolls_back_and_skips_chroma_cleanup(
    monkeypatch, seeded_documents
):
    from sqlalchemy import event

    db, owner, _, documents = seeded_documents
    target = documents[0]

    def fail_commit(_session):
        raise RuntimeError("synthetic transaction failure")

    event.listen(db, "before_commit", fail_commit)
    delete_vectors = Mock()
    monkeypatch.setattr(main, "delete_document_vectors", delete_vectors)
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, owner)

    try:
        with TestClient(main.app) as client:
            response = client.delete(f"/documents/{target.id}")
    finally:
        event.remove(db, "before_commit", fail_commit)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not delete document."}
    assert db.get(Document, target.id) is not None
    delete_vectors.assert_not_called()


def test_vector_store_helper_deletes_with_exact_document_metadata_filter(monkeypatch):
    collection = Mock()
    monkeypatch.setattr(vector_store, "collection", collection)
    document_id = str(uuid.uuid4())

    vector_store.delete_document_vectors(document_id)

    collection.delete.assert_called_once_with(where={"document_id": document_id})
