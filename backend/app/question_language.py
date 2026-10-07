"""Small script heuristic for supported deterministic replies, not full detection."""


def question_language(question: str) -> str:
    """Prefer the dominant Hindi/Telugu script; default to English.

    Devanagari is shared by other languages. Romanized and mixed-language
    questions may be misidentified; this deliberately avoids a provider call.
    """
    counts = {
        "hi": sum("\u0900" <= char <= "\u097f" and char.isalpha() for char in question),
        "te": sum("\u0c00" <= char <= "\u0c7f" and char.isalpha() for char in question),
    }
    language = max(counts, key=counts.get)
    return language if counts[language] else "en"


def no_results_response(question: str) -> str:
    return {
        "en": "The information is not available in the provided document.",
        "hi": "यह जानकारी दिए गए दस्तावेज़ में उपलब्ध नहीं है।",
        "te": "ఈ సమాచారం అందించిన పత్రంలో అందుబాటులో లేదు.",
    }[question_language(question)]
