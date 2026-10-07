import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import config, main
from backend.app.database import SessionLocal, get_db
from backend.app.auth import get_current_user
from backend.app.models import Conversation, Document, Message, User


class StubSession:
    def __init__(self, document, conversation=None, messages=None):
        self.document = document
        self.conversation = conversation
        self.messages = messages or []
        self.lookups = []
        self.added = []
        self.commits = 0
        self.message_query = None
        self.current_user_message_id = None

    def get(self, model, record_id):
        self.lookups.append((model, record_id))
        if model is Document:
            return self.document
        if model is Conversation and self.conversation is not None:
            return self.conversation if self.conversation.id == record_id else None
        return None

    def add(self, record):
        self.added.append(record)
        if isinstance(record, Conversation):
            self.conversation = record

    def commit(self):
        self.commits += 1
        if self.added and isinstance(self.added[-1], Message):
            message = self.added[-1]
            if message.created_at is None:
                message.created_at = datetime.now(timezone.utc)
            self.messages.append(message)
            if message.role == "user":
                self.current_user_message_id = message.id

    def rollback(self):
        pass

    def scalars(self, statement):
        self.message_query = statement

        class Results:
            def all(inner_self):
                return sorted(
                    [
                        message
                        for message in self.messages
                        if message.id != self.current_user_message_id
                    ],
                    key=lambda message: (message.created_at, str(message.id)),
                )

        return Results()


@pytest.fixture(autouse=True)
def disabled_retrieval_threshold(monkeypatch):
    monkeypatch.setattr(main, "RAG_MAX_DISTANCE", None)
    monkeypatch.setattr(main, "RAG_MAX_HISTORY_MESSAGES", 10)


def _use_session(monkeypatch, session, current_user=None):
    def override_get_db():
        yield session

    monkeypatch.setitem(main.app.dependency_overrides, get_db, override_get_db)
    if current_user is None:
        current_user = User(id=uuid.uuid4())
    if session.document is not None and session.document.user_id is None:
        session.document.user_id = current_user.id

    def override_current_user():
        return current_user

    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user,
        override_current_user,
    )
    return current_user


def test_ask_allows_existing_document_and_preserves_rag_response(monkeypatch):
    document_id = uuid.UUID("abcdef01-2345-4678-9abc-def012345678")
    conversation_id = str(uuid.uuid4())
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"))
    create_embedding = Mock(return_value=[0.25, 0.75])
    retrieved_documents = [
        {
            "text": "Customer ID: ACME-1042",
            "page": 1,
            "source": "synthetic.pdf",
            "distance": 0.1,
        }
    ]
    retrieved_documents.append(dict(retrieved_documents[0]))
    search_documents = Mock(return_value=retrieved_documents)
    history_at_generation = []

    def stub_generate_answer(question, documents, history):
        history_at_generation.append(list(history))
        return "The customer ID is ACME-1042. [Source: synthetic.pdf, Page 1]"

    generate_answer = Mock(side_effect=stub_generate_answer)
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "What is the customer ID?",
                "conversation_id": conversation_id,
                "document_id": str(document_id).upper(),
            },
        )

    assert response.status_code == 200
    assert session.lookups == [
        (Document, document_id),
        (Conversation, uuid.UUID(conversation_id)),
    ]
    assert isinstance(session.added[0], Conversation)
    assert session.added[0].id == uuid.UUID(conversation_id)
    assert session.added[0].document_id == document_id
    assert [type(record) for record in session.added] == [
        Conversation,
        Message,
        Message,
    ]
    assert [
        (record.role, record.content, record.conversation_id)
        for record in session.added[1:]
    ] == [
        ("user", "What is the customer ID?", uuid.UUID(conversation_id)),
        ("assistant", "The customer ID is ACME-1042. [Source: synthetic.pdf, Page 1]", uuid.UUID(conversation_id)),
    ]
    assert session.commits == 3
    create_embedding.assert_called_once_with("What is the customer ID?")
    search_documents.assert_called_once_with(
        [0.25, 0.75], top_k=5, document_id=str(document_id), max_distance=None
    )
    generate_answer.assert_called_once()
    assert generate_answer.call_args.args[:2] == (
        "What is the customer ID?",
        retrieved_documents,
    )
    assert history_at_generation == [[]]
    assert session.message_query is not None
    assert "messages.created_at ASC, messages.id ASC" in str(session.message_query)
    assert "messages.id !=" in str(session.message_query)
    assert response.json() == {
        "conversation_id": conversation_id,
        "question": "What is the customer ID?",
        "answer": "The customer ID is ACME-1042. [Source: synthetic.pdf, Page 1]",
        "sources": [{"source": "synthetic.pdf", "page": 1}],
    }


def test_ask_reuses_existing_conversation(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    existing = Conversation(id=conversation_id, document_id=document_id)
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"), existing)
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[]))
    monkeypatch.setattr(main, "search_documents", Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}]))
    monkeypatch.setattr(main, "generate_answer", Mock(return_value="Answer."))
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Sec-Fetch-Site": "same-origin",
            },
            json={
                "question": "Question?",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    assert session.lookups == [(Document, document_id), (Conversation, conversation_id)]
    assert len(session.added) == 2
    assert isinstance(session.added[0], Message)
    assert isinstance(session.added[1], Message)
    assert [record.role for record in session.added] == ["user", "assistant"]
    assert session.commits == 2


def test_ask_uses_chronological_postgres_history(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    existing = Conversation(id=conversation_id, document_id=document_id)
    earlier = Message(
        id=uuid.UUID(int=1),
        conversation_id=conversation_id,
        role="user",
        content="Earlier question",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    later = Message(
        id=uuid.UUID(int=2),
        conversation_id=conversation_id,
        role="assistant",
        content="Earlier answer",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf"),
        existing,
        messages=[later, earlier],
    )
    observed_history = []
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[]))
    monkeypatch.setattr(main, "search_documents", Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}]))
    monkeypatch.setattr(
        main,
        "generate_answer",
        Mock(side_effect=lambda question, documents, history: observed_history.extend(history) or "Answer."),
    )
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "Current question",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    assert observed_history == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    assert "Current question" not in [item["content"] for item in observed_history]


def test_ask_reloads_persisted_history_after_restart(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    session = SessionLocal()
    user = User(
        email=f"ask-restart-{uuid.uuid4()}@example.test",
        auth_provider="google",
        provider_user_id=str(uuid.uuid4()),
    )
    document = Document(
        id=document_id,
        filename="synthetic-restart-test.txt",
        user=user,
    )
    conversation = Conversation(id=conversation_id, document_id=document_id)
    previous_messages = [
        Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="user",
            content="Persisted previous question",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content="Persisted previous answer",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    ]
    generation_calls = []
    db_override = None
    try:
        session.add_all([user, document, conversation, *previous_messages])
        session.commit()

        monkeypatch.setattr(main, "create_embedding", Mock(return_value=[]))
        monkeypatch.setattr(main, "search_documents", Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}]))
        monkeypatch.setattr(
            main,
            "generate_answer",
            Mock(
                side_effect=lambda question, documents, history: generation_calls.append(
                    (question, list(history))
                ) or f"Synthetic answer {len(generation_calls)}."
            ),
        )

        def override_get_db():
            request_session = SessionLocal()
            try:
                yield request_session
            finally:
                request_session.close()

        def override_current_user():
            return user

        db_override = override_get_db
        monkeypatch.setitem(main.app.dependency_overrides, get_db, db_override)
        monkeypatch.setitem(
            main.app.dependency_overrides,
            get_current_user,
            override_current_user,
        )

        with TestClient(main.app) as first_client:
            first_response = first_client.post(
                "/ask",
                json={
                    "question": "First current question",
                    "conversation_id": str(conversation_id),
                    "document_id": str(document_id),
                },
            )

        with TestClient(main.app) as fresh_client:
            second_response = fresh_client.post(
                "/ask",
                json={
                    "question": "Second current question",
                    "conversation_id": str(conversation_id),
                    "document_id": str(document_id),
                },
            )

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert generation_calls == [
            (
                "First current question",
                [
                    {"role": "user", "content": "Persisted previous question"},
                    {"role": "assistant", "content": "Persisted previous answer"},
                ],
            ),
            (
                "Second current question",
                [
                    {"role": "user", "content": "Persisted previous question"},
                    {"role": "assistant", "content": "Persisted previous answer"},
                    {"role": "user", "content": "First current question"},
                    {"role": "assistant", "content": "Synthetic answer 1."},
                ],
            ),
        ]
        second_question, second_history = generation_calls[1]
        second_llm_input = [second_question] + [
            message["content"] for message in second_history
        ]
        assert second_llm_input.count("Second current question") == 1

        persisted_messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).all()
        assert [(message.role, message.content) for message in persisted_messages] == [
            ("user", "Persisted previous question"),
            ("assistant", "Persisted previous answer"),
            ("user", "First current question"),
            ("assistant", "Synthetic answer 1."),
            ("user", "Second current question"),
            ("assistant", "Synthetic answer 2."),
        ]
    finally:
        if db_override is not None:
            main.app.dependency_overrides.pop(get_db, None)
        session.rollback()
        session.query(Message).filter(
            Message.conversation_id == conversation_id
        ).delete(synchronize_session=False)
        saved_conversation = session.get(Conversation, conversation_id)
        if saved_conversation is not None:
            session.delete(saved_conversation)
        saved_document = session.get(Document, document_id)
        if saved_document is not None:
            session.delete(saved_document)
        saved_user = session.get(User, user.id)
        if saved_user is not None:
            session.delete(saved_user)
        session.commit()
        session.close()


def test_ask_keeps_user_message_without_assistant_when_generation_fails(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    existing = Conversation(id=conversation_id, document_id=document_id)
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"), existing)
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[]))
    monkeypatch.setattr(main, "search_documents", Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}]))
    monkeypatch.setattr(
        main,
        "generate_answer",
        Mock(side_effect=RuntimeError("generation failed")),
    )
    _use_session(monkeypatch, session)

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/ask",
            json={
                "question": "Question?",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 500
    assert len(session.added) == 1
    message = session.added[0]
    assert isinstance(message, Message)
    assert message.conversation_id == conversation_id
    assert message.role == "user"
    assert message.content == "Question?"
    assert session.commits == 1


def test_ask_rejects_conversation_for_different_document(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    other_document_id = uuid.uuid4()
    existing = Conversation(id=conversation_id, document_id=other_document_id)
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"), existing)
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Sec-Fetch-Site": "same-origin",
            },
            json={
                "question": "Question?",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert str(conversation_id) not in response.text
    assert str(other_document_id) not in response.text
    assert session.added == []
    assert session.commits == 0
    assert session.message_query is None
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


def test_ask_returns_404_before_rag_for_nonexistent_document(monkeypatch):
    document_id = uuid.uuid4()
    session = StubSession(None)
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "What is the customer ID?",
                "conversation_id": str(uuid.uuid4()),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert session.lookups == [(Document, document_id)]
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


def test_ask_requires_authentication_before_document_or_rag_work(monkeypatch):
    document_id = uuid.uuid4()
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf", user_id=uuid.uuid4())
    )
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)
    main.app.dependency_overrides.pop(get_current_user, None)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "hi",
                "conversation_id": str(uuid.uuid4()),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 401
    assert session.lookups == []
    assert session.added == []
    assert session.commits == 0
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


def test_ask_hides_document_owned_by_another_user_before_conversation_or_rag(
    monkeypatch,
):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    current_user = User(id=uuid.uuid4())
    other_owner_id = uuid.uuid4()
    session = StubSession(
        Document(
            id=document_id,
            filename="synthetic-private.pdf",
            user_id=other_owner_id,
        )
    )
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session, current_user=current_user)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Sec-Fetch-Site": "same-origin",
            },
            json={
                "question": "hello",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
    assert session.lookups == [(Document, document_id)]
    assert session.added == []
    assert session.commits == 0
    assert session.message_query is None
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


@pytest.mark.parametrize(
    ("question", "expected_answer"),
    [
        ("hi", "Hello! How can I help you with this document?"),
        ("hello", "Hello! How can I help you with this document?"),
        ("hey", "Hello! How can I help you with this document?"),
        ("thanks", "You're welcome!"),
        ("thank you", "You're welcome!"),
        ("nice", "Glad to help!"),
        ("cool", "Glad to help!"),
        ("bye", "Goodbye!"),
        ("नमस्ते", "नमस्ते! इस दस्तावेज़ के बारे में मैं आपकी कैसे मदद कर सकता हूँ?"),
        ("धन्यवाद", "आपका स्वागत है!"),
        ("शुक्रिया", "आपका स्वागत है!"),
        ("ठीक है", "मदद करके खुशी हुई!"),
        ("अलविदा", "अलविदा!"),
        ("నమస్తే", "నమస్తే! ఈ పత్రం గురించి మీకు ఎలా సహాయపడగలను?"),
        ("నమస్కారం", "నమస్తే! ఈ పత్రం గురించి మీకు ఎలా సహాయపడగలను?"),
        ("ధన్యవాదాలు", "మీకు స్వాగతం!"),
        ("సరే", "సహాయం చేయడం సంతోషంగా ఉంది!"),
        ("వీడ్కోలు", "వీడ్కోలు!"),
    ],
)
def test_exact_conversational_inputs_skip_rag_and_persist_messages(
    monkeypatch, question, expected_answer
):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    previous_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    conversation = Conversation(
        id=conversation_id,
        document_id=document_id,
        updated_at=previous_updated_at,
    )
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf"), conversation
    )
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": question,
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": str(conversation_id),
        "question": question,
        "answer": expected_answer,
        "sources": [],
    }
    assert [(message.role, message.content) for message in session.added] == [
        ("user", question),
        ("assistant", expected_answer),
    ]
    assert session.commits == 2
    assert conversation.updated_at > previous_updated_at
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


def test_conversational_intent_normalizes_case_and_whitespace(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf"),
        Conversation(id=conversation_id, document_id=document_id),
    )
    create_embedding = Mock()
    search_documents = Mock()
    generate_answer = Mock()
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "  HeLLo\t  ",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "Hello! How can I help you with this document?"
    assert response.json()["sources"] == []
    assert session.added[0].content == "  HeLLo\t  "
    create_embedding.assert_not_called()
    search_documents.assert_not_called()
    generate_answer.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "thanks, what is the renewal date?",
        "hello what is the SLA?",
        "नमस्ते, नवीनीकरण की तारीख क्या है?",
        "ధన్యవాదాలు, గడువు ఎప్పుడు?",
        "What is the renewal date?",
    ],
)
def test_non_exact_or_document_question_uses_rag(monkeypatch, question):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"))
    create_embedding = Mock(return_value=[0.1, 0.2])
    search_documents = Mock(
        return_value=[
            {"text": "Synthetic context", "source": "synthetic.pdf", "page": 1}
        ]
    )
    generate_answer = Mock(return_value="Grounded answer. [Source: synthetic.pdf, Page 1]")
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": question,
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    create_embedding.assert_called_once_with(question)
    search_documents.assert_called_once_with(
        [0.1, 0.2], top_k=5, document_id=str(document_id), max_distance=None
    )
    generate_answer.assert_called_once_with(question, search_documents.return_value, [])
    assert response.json()["sources"] == [{"source": "synthetic.pdf", "page": 1}]


def test_conversational_exchange_remains_in_history_for_follow_up_rag(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf"),
        Conversation(id=conversation_id, document_id=document_id),
    )
    create_embedding = Mock(return_value=[0.1, 0.2])
    search_documents = Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}])
    observed_histories = []

    def generate(question, documents, history):
        observed_histories.append(list(history))
        return "Grounded answer."

    generate_answer = Mock(side_effect=generate)
    _use_session(monkeypatch, session)
    monkeypatch.setattr(main, "create_embedding", create_embedding)
    monkeypatch.setattr(main, "search_documents", search_documents)
    monkeypatch.setattr(main, "generate_answer", generate_answer)

    with TestClient(main.app) as client:
        greeting_response = client.post(
            "/ask",
            json={
                "question": "hello",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )
        question_response = client.post(
            "/ask",
            json={
                "question": "What is the renewal date?",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert greeting_response.status_code == question_response.status_code == 200
    assert greeting_response.json()["sources"] == []
    assert observed_histories == [
        [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "Hello! How can I help you with this document?",
            },
        ]
    ]
    assert create_embedding.call_count == 1
    assert search_documents.call_count == 1
    assert generate_answer.call_count == 1


def test_ask_sends_only_recent_persisted_messages_to_generation(monkeypatch):
    document_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    conversation = Conversation(id=conversation_id, document_id=document_id)
    history = [
        Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=datetime(2026, 1, index + 1, tzinfo=timezone.utc),
        )
        for index, (role, content) in enumerate(
            [
                ("user", "old user question"),
                ("assistant", "old assistant answer"),
                ("user", "recent user question"),
                ("assistant", "recent assistant answer"),
            ]
        )
    ]
    session = StubSession(
        Document(id=document_id, filename="synthetic.pdf"),
        conversation,
        messages=history,
    )
    generate_answer = Mock(return_value="Grounded answer.")
    monkeypatch.setattr(main, "RAG_MAX_HISTORY_MESSAGES", 2)
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[0.1, 0.2]))
    monkeypatch.setattr(
        main,
        "search_documents",
        Mock(return_value=[{"text": "Context", "source": "synthetic.pdf", "page": 1}]),
    )
    monkeypatch.setattr(main, "generate_answer", generate_answer)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post(
            "/ask",
            json={
                "question": "What is the renewal date?",
                "conversation_id": str(conversation_id),
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 200
    generate_answer.assert_called_once_with(
        "What is the renewal date?",
        [{"text": "Context", "source": "synthetic.pdf", "page": 1}],
        [
            {"role": "user", "content": "recent user question"},
            {"role": "assistant", "content": "recent assistant answer"},
        ],
    )
    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "old user question"),
        ("assistant", "old assistant answer"),
        ("user", "recent user question"),
        ("assistant", "recent assistant answer"),
        ("user", "What is the renewal date?"),
        ("assistant", "Grounded answer."),
    ]


@pytest.mark.parametrize("setting, expected", [(None, 10), ("", 10), ("  ", 10), (" 4 ", 4)])
def test_rag_history_configuration(setting, expected):
    assert config._parse_rag_max_history_messages(setting) == expected


@pytest.mark.parametrize("setting", ["invalid", "0", "-1", "1.5"])
def test_rag_history_configuration_rejects_invalid_values(setting):
    with pytest.raises(RuntimeError, match="RAG_MAX_HISTORY_MESSAGES must be a positive integer"):
        config._parse_rag_max_history_messages(setting)


@pytest.mark.parametrize("threshold", [None, 0.5])
@pytest.mark.parametrize("question,unavailable", [
    ("What is the customer ID?", "The information is not available in the provided document."),
    ("ग्राहक की पहचान क्या है?", "यह जानकारी दिए गए दस्तावेज़ में उपलब्ध नहीं है।"),
    ("కస్టమర్ గుర్తింపు ఏమిటి?", "ఈ సమాచారం అందించిన పత్రంలో అందుబాటులో లేదు."),
])
def test_empty_retrieval_skips_generation_and_persists_unavailable(monkeypatch, threshold, question, unavailable):
    document_id = uuid.UUID("abcdef01-2345-4678-9abc-def012345678")
    conversation_id = uuid.uuid4()
    previous_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    conversation = Conversation(
        id=conversation_id, document_id=document_id, updated_at=previous_updated_at
    )
    session = StubSession(Document(id=document_id, filename="synthetic.pdf"), conversation)
    retrieval = Mock(return_value=[])
    generation = Mock()
    monkeypatch.setattr(main, "RAG_MAX_DISTANCE", threshold)
    monkeypatch.setattr(main, "create_embedding", Mock(return_value=[0.25, 0.75]))
    monkeypatch.setattr(main, "search_documents", retrieval)
    monkeypatch.setattr(main, "generate_answer", generation)
    _use_session(monkeypatch, session)

    with TestClient(main.app) as client:
        response = client.post("/ask", json={
            "question": question,
            "conversation_id": str(conversation_id),
            "document_id": str(document_id).upper(),
        })

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": str(conversation_id), "question": question,
        "answer": unavailable, "sources": [],
    }
    retrieval.assert_called_once_with(
        [0.25, 0.75], top_k=5, document_id=str(document_id), max_distance=threshold
    )
    generation.assert_not_called()
    assert [(message.role, message.content) for message in session.messages] == [
        ("user", question), ("assistant", unavailable),
    ]
    assert all(message.conversation_id == conversation_id for message in session.messages)
    assert session.commits == 2
    assert conversation.updated_at > previous_updated_at


@pytest.mark.parametrize("setting, expected", [(None, None), ("", None), ("  ", None), (" 0.5 ", 0.5)])
def test_rag_distance_configuration(setting, expected):
    assert config._parse_rag_max_distance(setting) == expected


@pytest.mark.parametrize("setting", ["invalid", "nan", "inf", "-inf"])
def test_rag_distance_configuration_rejects_invalid_values(setting):
    with pytest.raises(RuntimeError, match="RAG_MAX_DISTANCE must be a finite numeric value"):
        config._parse_rag_max_distance(setting)
