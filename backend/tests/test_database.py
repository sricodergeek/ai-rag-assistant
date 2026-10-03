import uuid

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app.database import SessionLocal, engine
from backend.app.models import Conversation, Document, Message, User


def test_database_connectivity_and_documents_table():
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1

    assert inspect(engine).has_table("documents")


def test_conversations_table_columns_and_document_foreign_key():
    inspector = inspect(engine)

    assert inspector.has_table("conversations")
    assert {
        "id",
        "document_id",
        "created_at",
        "updated_at",
    } <= {column["name"] for column in inspector.get_columns("conversations")}
    assert {
        "id",
        "user_id",
        "filename",
        "created_at",
    } == {column["name"] for column in inspector.get_columns("documents")}
    foreign_keys = inspector.get_foreign_keys("conversations")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["document_id"]
    assert foreign_keys[0]["referred_table"] == "documents"
    assert foreign_keys[0]["referred_columns"] == ["id"]


def test_documents_user_foreign_key():
    inspector = inspect(engine)
    foreign_keys = inspect(engine).get_foreign_keys("documents")
    user_foreign_keys = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key["constrained_columns"] == ["user_id"]
    ]

    assert len(user_foreign_keys) == 1
    assert user_foreign_keys[0]["referred_table"] == "users"
    assert user_foreign_keys[0]["referred_columns"] == ["id"]
    user_id_column = next(
        column for column in inspector.get_columns("documents")
        if column["name"] == "user_id"
    )
    assert user_id_column["nullable"] is False


def test_document_cannot_be_created_without_user_id():
    session = SessionLocal()
    document = Document(filename="synthetic-owner-required.txt", user_id=None)
    try:
        session.add(document)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Document without user_id was accepted")
    finally:
        session.rollback()
        session.query(Document).filter(
            Document.filename == "synthetic-owner-required.txt"
        ).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_document_user_relationships_with_valid_owner():
    session = SessionLocal()
    user_email = f"document-owner-{uuid.uuid4()}@example.test"
    user = User(
        email=user_email,
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )
    document_id = uuid.uuid4()
    owned_document = Document(
        id=document_id,
        filename="synthetic-owned-document.txt",
        user=user,
    )
    try:
        session.add_all([user, owned_document])
        session.commit()
        session.refresh(user)
        session.refresh(owned_document)

        assert owned_document.user_id == user.id
        assert owned_document.user == user
        assert owned_document in user.documents
    finally:
        session.rollback()
        session.query(Document).filter(
            Document.id == document_id
        ).delete(synchronize_session=False)
        session.query(User).filter(User.email == user_email).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


def test_messages_table_columns_and_conversation_foreign_key():
    inspector = inspect(engine)

    assert inspector.has_table("messages")
    assert [
        column["name"] for column in inspector.get_columns("messages")
    ] == ["id", "conversation_id", "role", "content", "created_at"]

    foreign_keys = inspector.get_foreign_keys("messages")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["conversation_id"]
    assert foreign_keys[0]["referred_table"] == "conversations"
    assert foreign_keys[0]["referred_columns"] == ["id"]


def test_message_belongs_to_conversation():
    conversation = Conversation(document_id=uuid.uuid4())
    message = Message(role="user", content="Synthetic test message")

    conversation.messages.append(message)

    assert message.conversation is conversation


def test_user_creation_generates_uuid():
    session = SessionLocal()
    email = f"user-{uuid.uuid4()}@example.test"
    user = User(
        email=email,
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )
    try:
        session.add(user)
        session.commit()
        session.refresh(user)

        assert isinstance(user.id, uuid.UUID)
        assert user.email == email
        assert user.created_at is not None
    finally:
        session.rollback()
        session.query(User).filter(User.email == email).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


def test_user_email_uniqueness_is_enforced():
    session = SessionLocal()
    email = f"duplicate-{uuid.uuid4()}@example.test"
    original = User(
        email=email,
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )
    try:
        session.add(original)
        session.commit()
        session.add(
            User(
                email=email,
                auth_provider="github",
                provider_user_id=str(uuid.uuid4()),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Duplicate user email was accepted")
    finally:
        session.rollback()
        session.query(User).filter(User.email == email).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


def test_user_provider_identity_uniqueness_is_enforced():
    session = SessionLocal()
    provider_user_id = str(uuid.uuid4())
    first_email = f"provider-first-{uuid.uuid4()}@example.test"
    second_email = f"provider-second-{uuid.uuid4()}@example.test"
    try:
        session.add(
            User(
                email=first_email,
                auth_provider="google",
                provider_user_id=provider_user_id,
            )
        )
        session.commit()
        session.add(
            User(
                email=second_email,
                auth_provider="google",
                provider_user_id=provider_user_id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Duplicate provider identity was accepted")
    finally:
        session.rollback()
        session.query(User).filter(User.email.in_([first_email, second_email])).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


def test_user_name_and_avatar_url_can_be_null():
    session = SessionLocal()
    email = f"nullable-{uuid.uuid4()}@example.test"
    user = User(
        email=email,
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
        name=None,
        avatar_url=None,
    )
    try:
        session.add(user)
        session.commit()
        session.refresh(user)

        assert user.name is None
        assert user.avatar_url is None
    finally:
        session.rollback()
        session.query(User).filter(User.email == email).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()
