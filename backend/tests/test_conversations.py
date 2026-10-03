import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.auth import get_current_user
from backend.app.database import SessionLocal, get_db
from backend.app.models import Conversation, Document, Message, User


def _new_user(prefix: str) -> User:
    return User(
        email=f"{prefix}-{uuid.uuid4()}@example.test",
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )


@pytest.fixture
def conversation_data():
    db = SessionLocal()
    owner = _new_user("conversation-owner")
    other_user = _new_user("conversation-other")
    document = Document(
        id=uuid.uuid4(), user=owner, filename="conversation-test.pdf"
    )
    another_owned_document = Document(
        id=uuid.uuid4(), user=owner, filename="another-owned.pdf"
    )
    other_document = Document(
        id=uuid.uuid4(), user=other_user, filename="other-user.pdf"
    )
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    middle = datetime(2026, 1, 2, tzinfo=timezone.utc)
    recent = datetime(2026, 1, 3, tzinfo=timezone.utc)
    first_conversation = Conversation(
        id=uuid.uuid4(),
        document=document,
        created_at=old,
        updated_at=middle,
    )
    empty_conversation = Conversation(
        id=uuid.uuid4(),
        document=document,
        created_at=middle,
        updated_at=recent,
    )
    another_document_conversation = Conversation(
        id=uuid.uuid4(), document=another_owned_document, created_at=old, updated_at=old
    )
    other_user_conversation = Conversation(
        id=uuid.uuid4(), document=other_document, created_at=old, updated_at=old
    )
    messages = [
        Message(
            id=uuid.UUID(int=1),
            conversation=first_conversation,
            role="user",
            content="  First question\nwith a second line  ",
            created_at=old,
        ),
        Message(
            id=uuid.UUID(int=2),
            conversation=first_conversation,
            role="assistant",
            content="First answer",
            created_at=middle,
        ),
        Message(
            id=uuid.UUID(int=3),
            conversation=first_conversation,
            role="user",
            content="Later question",
            created_at=recent,
        ),
        Message(
            id=uuid.UUID(int=4),
            conversation=first_conversation,
            role="assistant",
            content="Later answer",
            created_at=recent,
        ),
        Message(
            id=uuid.UUID(int=5),
            conversation=another_document_conversation,
            role="user",
            content="Different owned document conversation",
            created_at=old,
        ),
        Message(
            id=uuid.UUID(int=6),
            conversation=other_user_conversation,
            role="user",
            content="Private other-user message",
            created_at=old,
        ),
    ]
    db.add_all(
        [
            owner,
            other_user,
            document,
            another_owned_document,
            other_document,
            first_conversation,
            empty_conversation,
            another_document_conversation,
            other_user_conversation,
            *messages,
        ]
    )
    db.commit()
    data = {
        "db": db,
        "owner": owner,
        "other_user": other_user,
        "document": document,
        "another_owned_document": another_owned_document,
        "other_document": other_document,
        "first_conversation": first_conversation,
        "empty_conversation": empty_conversation,
        "another_document_conversation": another_document_conversation,
        "other_user_conversation": other_user_conversation,
        "messages": messages,
    }
    try:
        yield data
    finally:
        db.rollback()
        document_ids = [
            data["document"].id,
            data["another_owned_document"].id,
            data["other_document"].id,
        ]
        conversation_ids = db.query(Conversation.id).filter(
            Conversation.document_id.in_(document_ids)
        )
        db.query(Message).filter(
            Message.conversation_id.in_(conversation_ids)
        ).delete(synchronize_session=False)
        db.query(Conversation).filter(
            Conversation.document_id.in_(document_ids)
        ).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(document_ids)).delete(
            synchronize_session=False
        )
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
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, lambda: user)


def test_conversation_list_requires_authentication(monkeypatch, conversation_data):
    data = conversation_data
    _override_db(monkeypatch, data["db"])

    with TestClient(main.app) as client:
        response = client.get(f"/documents/{data['document'].id}/conversations")

    assert response.status_code == 401


def test_owner_lists_only_conversations_for_authorized_document(
    monkeypatch, conversation_data
):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(f"/documents/{data['document'].id}/conversations")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [
        str(data["empty_conversation"].id),
        str(data["first_conversation"].id),
    ]
    assert all(
        set(item) == {"id", "created_at", "updated_at", "title"}
        for item in payload
    )
    assert str(data["other_user_conversation"].id) not in response.text
    assert str(data["another_document_conversation"].id) not in response.text
    assert str(data["owner"].id) not in response.text


def test_empty_conversation_list_returns_empty_array(monkeypatch, conversation_data):
    data = conversation_data
    db = data["db"]
    empty_document = Document(
        id=uuid.uuid4(), user_id=data["owner"].id, filename="no-conversations.pdf"
    )
    db.add(empty_document)
    db.commit()
    _override_db(monkeypatch, db)
    _override_user(monkeypatch, data["owner"])

    try:
        with TestClient(main.app) as client:
            response = client.get(f"/documents/{empty_document.id}/conversations")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        db.rollback()
        saved_document = db.get(Document, empty_document.id)
        if saved_document is not None:
            db.delete(saved_document)
            db.commit()


def test_other_users_document_returns_generic_document_404(
    monkeypatch, conversation_data
):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(
            f"/documents/{data['other_document'].id}/conversations"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert str(data["other_document"].id) not in response.text


def test_conversation_list_orders_by_latest_updated_at_and_derives_titles(
    monkeypatch, conversation_data
):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(f"/documents/{data['document'].id}/conversations")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == str(data["empty_conversation"].id)
    assert payload[0]["title"] == "New conversation"
    assert payload[1]["id"] == str(data["first_conversation"].id)
    assert payload[1]["title"] == "First question with a second line"


def test_message_history_requires_authentication(monkeypatch, conversation_data):
    data = conversation_data
    _override_db(monkeypatch, data["db"])

    with TestClient(main.app) as client:
        response = client.get(
            f"/documents/{data['document'].id}/conversations/"
            f"{data['first_conversation'].id}/messages"
        )

    assert response.status_code == 401


def test_owner_retrieves_messages_in_chronological_order(monkeypatch, conversation_data):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(
            f"/documents/{data['document'].id}/conversations/"
            f"{data['first_conversation'].id}/messages"
        )

    assert response.status_code == 200
    payload = response.json()
    assert [(item["role"], item["content"]) for item in payload] == [
        ("user", "  First question\nwith a second line  "),
        ("assistant", "First answer"),
        ("user", "Later question"),
        ("assistant", "Later answer"),
    ]
    assert all(
        set(item) == {"id", "role", "content", "created_at"}
        for item in payload
    )
    assert all(item["id"] for item in payload)
    assert str(data["owner"].id) not in response.text


def test_messages_for_another_users_document_return_generic_404(
    monkeypatch, conversation_data
):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(
            f"/documents/{data['other_document'].id}/conversations/"
            f"{data['other_user_conversation'].id}/messages"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert str(data["other_user_conversation"].id) not in response.text
    assert str(data["other_document"].id) not in response.text


def test_conversation_mismatch_returns_generic_404_without_leaking_ids(
    monkeypatch, conversation_data
):
    data = conversation_data
    _override_db(monkeypatch, data["db"])
    _override_user(monkeypatch, data["owner"])

    with TestClient(main.app) as client:
        response = client.get(
            f"/documents/{data['document'].id}/conversations/"
            f"{data['another_document_conversation'].id}/messages"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert str(data["another_document_conversation"].id) not in response.text
    assert str(data["another_owned_document"].id) not in response.text


def test_ask_updates_conversation_activity_timestamp(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    previous_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    current_user = User(id=uuid.uuid4())
    conversation = Conversation(
        id=conversation_id,
        document_id=document_id,
        updated_at=previous_updated_at,
    )
    document = Document(
        id=document_id,
        user_id=current_user.id,
        filename="synthetic.pdf",
    )

    class AskStubSession:
        def __init__(self):
            self.added = []

        def get(self, model, record_id):
            if model is Document and record_id == document_id:
                return document
            if model is Conversation and record_id == conversation_id:
                return conversation
            return None

        def add(self, record):
            self.added.append(record)

        def commit(self):
            pass

        def rollback(self):
            pass

        def scalars(self, statement):
            class Results:
                @staticmethod
                def all():
                    return []

            return Results()

    session = AskStubSession()
    committed_activity_timestamps = []
    original_commit = session.commit

    def capture_commit():
        committed_activity_timestamps.append(conversation.updated_at)
        original_commit()

    session.commit = capture_commit
    monkeypatch.setattr(main, "create_embedding", lambda _: [])
    monkeypatch.setattr(main, "search_documents", lambda *args, **kwargs: [])
    monkeypatch.setattr(main, "generate_answer", lambda *args: "Synthetic answer")

    def override_get_db():
        yield session

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user,
        lambda: current_user,
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "Synthetic question",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    assert len(committed_activity_timestamps) == 2
    assert all(timestamp > previous_updated_at for timestamp in committed_activity_timestamps)
