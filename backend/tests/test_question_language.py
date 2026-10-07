import pytest

from backend.app.intent_router import classify_intent, conversational_response
from backend.app.question_language import question_language


@pytest.mark.parametrize("text,language", [
    ("What is the policy?", "en"),
    ("नीति क्या है?", "hi"),
    ("విధానం ఏమిటి?", "te"),
    ("What does नीति mean?", "hi"),
    ("What does విధానం mean?", "te"),
    ("123 ।", "en"),
    ("niti kya hai", "en"),  # Romanization cannot be resolved by script.
])
def test_question_script_heuristic(text, language):
    assert question_language(text) == language


@pytest.mark.parametrize("text,intent", [("  ठीक\tहै  ", "acknowledgment"), ("  ధన్యవాదాలు  ", "thanks")])
def test_localized_intents_normalize_whitespace(text, intent):
    assert classify_intent(text) == intent


def test_existing_response_default_remains_english():
    assert conversational_response("thanks") == "You're welcome!"
    with pytest.raises(ValueError):
        conversational_response("rag", "hi")
