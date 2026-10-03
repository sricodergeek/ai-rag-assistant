from typing import Literal


Intent = Literal["greeting", "thanks", "acknowledgment", "sign_off", "rag"]


_INTENTS: dict[str, Intent] = {
    "hi": "greeting",
    "hello": "greeting",
    "hey": "greeting",
    "thanks": "thanks",
    "thank you": "thanks",
    "nice": "acknowledgment",
    "cool": "acknowledgment",
    "bye": "sign_off",
}


_RESPONSES: dict[Intent, str] = {
    "greeting": "Hello! How can I help you with this document?",
    "thanks": "You're welcome!",
    "acknowledgment": "Glad to help!",
    "sign_off": "Goodbye!",
}


def classify_intent(text: str) -> Intent:
    """Classify only exact, short conversational phrases; default to RAG."""
    normalized = " ".join(text.split()).casefold()
    return _INTENTS.get(normalized, "rag")


def conversational_response(intent: Intent) -> str:
    """Return the deterministic reply for a non-RAG intent."""
    if intent == "rag":
        raise ValueError("RAG intent does not have a conversational response.")
    return _RESPONSES[intent]
