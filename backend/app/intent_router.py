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
    "नमस्ते": "greeting",
    "धन्यवाद": "thanks",
    "शुक्रिया": "thanks",
    "ठीक है": "acknowledgment",
    "अलविदा": "sign_off",
    "నమస్తే": "greeting",
    "నమస్కారం": "greeting",
    "ధన్యవాదాలు": "thanks",
    "సరే": "acknowledgment",
    "వీడ్కోలు": "sign_off",
}


_RESPONSES: dict[Intent, str] = {
    "greeting": "Hello! How can I help you with this document?",
    "thanks": "You're welcome!",
    "acknowledgment": "Glad to help!",
    "sign_off": "Goodbye!",
}

_LOCALIZED_RESPONSES: dict[str, dict[Intent, str]] = {
    "hi": {
        "greeting": "नमस्ते! इस दस्तावेज़ के बारे में मैं आपकी कैसे मदद कर सकता हूँ?",
        "thanks": "आपका स्वागत है!",
        "acknowledgment": "मदद करके खुशी हुई!",
        "sign_off": "अलविदा!",
    },
    "te": {
        "greeting": "నమస్తే! ఈ పత్రం గురించి మీకు ఎలా సహాయపడగలను?",
        "thanks": "మీకు స్వాగతం!",
        "acknowledgment": "సహాయం చేయడం సంతోషంగా ఉంది!",
        "sign_off": "వీడ్కోలు!",
    },
}


def classify_intent(text: str) -> Intent:
    """Classify only exact, short conversational phrases; default to RAG."""
    normalized = " ".join(text.split()).casefold()
    return _INTENTS.get(normalized, "rag")


def conversational_response(intent: Intent, language: str = "en") -> str:
    """Return the deterministic reply for a non-RAG intent."""
    if intent == "rag":
        raise ValueError("RAG intent does not have a conversational response.")
    return _LOCALIZED_RESPONSES.get(language, _RESPONSES)[intent]
