from datetime import datetime, timezone
import logging
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user, router as auth_router
from backend.app.config import (
    FRONTEND_URL,
    RAG_MAX_DISTANCE,
    RAG_MAX_HISTORY_MESSAGES,
)
from backend.app.database import get_db
from backend.app.embeddings import create_embedding
from backend.app.fetch_metadata import validate_fetch_metadata
from backend.app.intent_router import classify_intent, conversational_response
from backend.app.ingestion import ingest_pdf
from backend.app.llm import generate_answer
from backend.app.models import Conversation, Document, Message, User
from backend.app.origin_validation import validate_frontend_origin
from backend.app.vector_store import delete_document_vectors, search_documents
from backend.app.transcription import MAX_AUDIO_BYTES, audio_format, transcribe_audio
from backend.app.question_language import no_results_response, question_language
from backend.app.speech import generate_speech, validate_speech_text
from backend.app.voice_usage import QuotaExhausted, UsageUnavailable, get_voice_usage, reserve_voice


logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: str
    conversation_id: str
    document_id: str


class SpeakRequest(BaseModel):
    text: str
    document_id: str | None = None


def _reserve_voice(user_id, feature):
    try:
        reserve_voice(user_id, feature)
    except QuotaExhausted as error:
        raise HTTPException(status_code=429, detail={
            "code": "voice_quota_exhausted", "feature": error.feature,
            **error.usage.to_dict(),
        }) from None
    except UsageUnavailable:
        raise HTTPException(status_code=503, detail="Voice usage is unavailable. Please try again.") from None


def _conversation_title(first_user_message: str | None) -> str:
    title = " ".join((first_user_message or "").split())
    if not title:
        return "New conversation"
    if len(title) > 80:
        return f"{title[:77].rstrip()}..."
    return title


app = FastAPI(
    title="AI Knowledge Assistant",
    description="RAG-based document question answering API",
    version="0.3.0",
)
app.include_router(auth_router)

_frontend = urlsplit(FRONTEND_URL)
_frontend_origin = f"{_frontend.scheme}://{_frontend.netloc}"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def root():
    return {
        "message": "AI Knowledge Assistant API is running",
        "version": app.version,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/documents")
def list_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        documents = db.scalars(
            select(Document)
            .where(Document.user_id == current_user.id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        ).all()
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not load documents.",
        ) from error

    return [
        {
            "id": str(document.id),
            "filename": document.filename,
            "created_at": document.created_at.isoformat(),
        }
        for document in documents
    ]


@app.get("/documents/{document_id}/conversations")
def list_document_conversations(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        requested_document_id = uuid.UUID(document_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error

    try:
        document = db.scalar(
            select(Document).where(
                Document.id == requested_document_id,
                Document.user_id == current_user.id,
            )
        )
    except Exception as error:
        db.rollback()
        logger.exception("Could not look up document for conversation listing.")
        raise HTTPException(
            status_code=500,
            detail="Could not load conversations.",
        ) from error

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    first_user_message = (
        select(Message.content)
        .where(
            Message.conversation_id == Conversation.id,
            Message.role == "user",
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    try:
        rows = db.execute(
            select(Conversation, first_user_message.label("first_user_message"))
            .where(Conversation.document_id == requested_document_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()
    except Exception as error:
        db.rollback()
        logger.exception("Could not load conversations for document.")
        raise HTTPException(
            status_code=500,
            detail="Could not load conversations.",
        ) from error

    return [
        {
            "id": str(conversation.id),
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "title": _conversation_title(title),
        }
        for conversation, title in rows
    ]


@app.get("/documents/{document_id}/conversations/{conversation_id}/messages")
def list_conversation_messages(
    document_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        requested_document_id = uuid.UUID(document_id)
        requested_conversation_id = uuid.UUID(conversation_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error

    try:
        document = db.scalar(
            select(Document).where(
                Document.id == requested_document_id,
                Document.user_id == current_user.id,
            )
        )
    except Exception as error:
        db.rollback()
        logger.exception("Could not look up document for message history.")
        raise HTTPException(
            status_code=500,
            detail="Could not load conversation history.",
        ) from error

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == requested_conversation_id,
                Conversation.document_id == requested_document_id,
            )
        )
    except Exception as error:
        db.rollback()
        logger.exception("Could not look up conversation history.")
        raise HTTPException(
            status_code=500,
            detail="Could not load conversation history.",
        ) from error

    if conversation is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == requested_conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).all()
    except Exception as error:
        db.rollback()
        logger.exception("Could not load messages for conversation.")
        raise HTTPException(
            status_code=500,
            detail="Could not load conversation history.",
        ) from error

    return [
        {
            "id": str(message.id),
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in messages
    ]


@app.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        requested_document_id = uuid.UUID(document_id)
    except (ValueError, AttributeError) as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error

    try:
        document = db.scalar(
            select(Document).where(
                Document.id == requested_document_id,
                Document.user_id == current_user.id,
            )
        )
    except Exception as error:
        logger.exception("Could not look up document for deletion.")
        raise HTTPException(
            status_code=500,
            detail="Could not delete document.",
        ) from error

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        conversation_ids = select(Conversation.id).where(
            Conversation.document_id == requested_document_id
        )
        db.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        )
        db.execute(
            delete(Conversation).where(
                Conversation.document_id == requested_document_id
            )
        )
        db.delete(document)
        db.commit()
    except Exception as error:
        logger.exception("PostgreSQL document deletion failed for %s.", requested_document_id)
        try:
            db.rollback()
        except Exception:
            logger.exception("PostgreSQL rollback failed for document deletion.")
        raise HTTPException(
            status_code=500,
            detail="Could not delete document.",
        ) from error

    try:
        delete_document_vectors(str(requested_document_id))
    except Exception as error:
        logger.exception("Chroma cleanup failed for deleted document %s.", requested_document_id)
        raise HTTPException(
            status_code=500,
            detail="Document was deleted, but vector cleanup could not be completed.",
        ) from error

    return Response(status_code=204)


@app.post("/transcribe")
def transcribe(
    file: UploadFile = File(...),
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
    current_user: User = Depends(get_current_user),
):
    try:
        data = file.file.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(status_code=400, detail="The recording is empty.")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio must be 10 MB or smaller.")
        extension = audio_format(file.filename or "", data)
        if extension is None:
            raise HTTPException(status_code=415, detail="Unsupported or invalid audio file.")
        _reserve_voice(current_user.id, "stt")
        try:
            return transcribe_audio(data, extension)
        except ValueError:
            raise HTTPException(status_code=422, detail="No speech was detected. Please try again.") from None
        except Exception:
            # Do not expose provider messages, credentials, or uploaded audio.
            logger.warning("Audio transcription failed.")
            raise HTTPException(status_code=502, detail="Transcription is unavailable. Please try again.") from None
    finally:
        file.file.close()


@app.get("/voice/usage")
def voice_usage(current_user: User = Depends(get_current_user)):
    try:
        return get_voice_usage(current_user.id)
    except UsageUnavailable:
        raise HTTPException(status_code=503, detail="Voice usage is unavailable. Please try again.") from None


@app.post("/speak")
def speak(
    request: SpeakRequest,
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        validate_speech_text(request.text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if request.document_id is not None:
        try:
            document_id = uuid.UUID(request.document_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Document not found.") from None
        document = db.get(Document, document_id)
        if document is None or document.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Document not found.")
    _reserve_voice(current_user.id, "tts")
    try:
        audio, content_type = generate_speech(request.text)
    except Exception:
        logger.warning("Speech generation failed.")
        raise HTTPException(status_code=502, detail="Voice answers are unavailable. Please try again.") from None
    return Response(content=audio, media_type=content_type, headers={"Cache-Control": "no-store"})


@app.post("/ask")
def ask(
    request: AskRequest,
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        requested_document_id = uuid.UUID(request.document_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Document not found.") from error

    document = db.get(Document, requested_document_id)
    if document is None or document.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        requested_conversation_id = uuid.UUID(request.conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid conversation ID.") from error

    conversation = db.get(Conversation, requested_conversation_id)
    if conversation is None:
        conversation = Conversation(
            id=requested_conversation_id,
            document_id=requested_document_id,
        )
        try:
            db.add(conversation)
            db.commit()
        except Exception as error:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Could not save the conversation record.",
            ) from error
    elif conversation.document_id != requested_document_id:
        raise HTTPException(status_code=404, detail="Document not found.")

    user_message = Message(
        id=uuid.uuid4(),
        conversation_id=requested_conversation_id,
        role="user",
        content=request.question,
    )
    try:
        conversation.updated_at = datetime.now(timezone.utc)
        db.add(user_message)
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save the user message.",
        ) from error

    persisted_messages = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == requested_conversation_id,
            Message.id != user_message.id,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    generation_history = [
        {"role": message.role, "content": message.content}
        for message in persisted_messages
    ]
    intent = classify_intent(request.question)
    documents = []
    if intent == "rag":
        embedding = create_embedding(request.question)
        documents = search_documents(
            embedding, top_k=5, document_id=str(requested_document_id),
            max_distance=RAG_MAX_DISTANCE,
        )
        if documents == []:
            answer = no_results_response(request.question)
        else:
            answer = generate_answer(
                request.question,
                documents,
                generation_history[-RAG_MAX_HISTORY_MESSAGES:],
            )
    else:
        answer = conversational_response(intent, question_language(request.question))

    assistant_message = Message(
        id=uuid.uuid4(),
        conversation_id=requested_conversation_id,
        role="assistant",
        content=answer,
    )
    try:
        conversation.updated_at = datetime.now(timezone.utc)
        db.add(assistant_message)
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save the assistant message.",
        ) from error

    sources = []
    seen_sources = set()
    for document in documents:
        source_page = (document["source"], document["page"])
        if source_page not in seen_sources:
            sources.append({"source": document["source"], "page": document["page"]})
            seen_sources.add(source_page)

    return {
        "conversation_id": request.conversation_id,
        "question": request.question,
        "answer": answer,
        "sources": sources,
    }


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    _fetch_metadata_validated: None = Depends(validate_fetch_metadata),
    _origin_validated: None = Depends(validate_frontend_origin),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uploaded_name = file.filename or ""
    filename = Path(uploaded_name.replace("\\", "/")).name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF.")

    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        filename=filename,
        user_id=current_user.id,
    )
    try:
        db.add(document)
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not save the document record.",
        ) from error

    try:
        documents_dir = Path(__file__).resolve().parents[1] / "documents"
        documents_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=documents_dir) as temporary_dir:
            saved_path = Path(temporary_dir) / filename
            saved_path.write_bytes(await file.read())
            chunks_ingested, _ = ingest_pdf(
                str(saved_path), document_id=str(document_id)
            )
    except Exception as error:
        try:
            db.delete(document)
            db.commit()
        except Exception as cleanup_error:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="PDF ingestion failed and the document record could not be removed.",
            ) from cleanup_error
        raise HTTPException(
            status_code=500,
            detail="PDF ingestion failed; the document record was removed.",
        ) from error

    return {
        "filename": filename,
        "document_id": str(document_id),
        "chunks_ingested": chunks_ingested,
        "message": "PDF ingested successfully.",
    }
