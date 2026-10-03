import re
from pathlib import Path
from uuid import uuid4

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


_UNAVAILABLE_PATTERN = re.compile(
    r"(?:not available|does not (?:provide|state|specify|contain)|"
    r"doesn't (?:provide|state|specify|contain)|not (?:provided|stated|specified)|"
    r"not included|cannot be determined|can't be determined|"
    r"unable to determine|don't have that information|do not have that information)",
    re.IGNORECASE,
)

_UNKNOWN_VALUE_PATTERNS = {
    "annual revenue": re.compile(
        r"\b(?:annual\s+)?revenue\b.{0,60}"
        r"(?:\$\s?\d|\bUSD\s?\d|\d[\d,.]*\s*(?:million|billion|thousand)\b)",
        re.IGNORECASE,
    ),
    "CEO": re.compile(
        r"\b(?:CEO|chief executive officer)\s*(?:is|:|\u2014)\s*"
        r"(?!not\b|unknown\b|unavailable\b|not provided\b|not specified\b)"
        r"[A-Z][A-Za-z'\u2019-]+(?:\s+[A-Z][A-Za-z'\u2019-]+){0,2}\b",
        re.IGNORECASE,
    ),
    "headquarters address": re.compile(
        r"\b(?:headquarters(?:\s+address)?|HQ|address)\s*(?:is|:|\u2014)\s*"
        r"(?!not\b|unknown\b|unavailable\b|not provided\b|not specified\b)"
        r"(?:\d{1,6}\s+[\w.-]+(?:\s+[\w.-]+){0,4}\s+"
        r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
        r"Lane|Ln\.?|Drive|Dr\.?)\b|"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2}\b)",
        re.IGNORECASE,
    ),
    "programming language": re.compile(
        r"\b(?:Python|JavaScript|TypeScript|Java|C\+\+|C#|Ruby|Rust|Go|"
        r"PHP|Swift|Kotlin|Scala|Perl|R)\b",
        re.IGNORECASE,
    ),
    "founding date": re.compile(
        r"\b(?:founded|established|incorporated)\s+(?:in\s+)?(?:18|19|20)\d{2}\b",
        re.IGNORECASE,
    ),
}


def test_real_rag_answers_unknown_questions_with_supported_refusals():
    require_live_evaluation()
    import chromadb

    from backend.app.chunker import chunk_pdf
    from backend.app.embeddings import create_embedding
    from backend.app.llm import generate_answer

    vector_store = load_evaluation_vector_store()
    pdf_path = Path(__file__).parent / "fixtures" / "rag_eval_document.pdf"
    chunks = chunk_pdf(str(pdf_path))

    evaluation_cases = [
        ("What is the customer's annual revenue?", "annual revenue"),
        ("Who is the customer's CEO?", "CEO"),
        ("What is the customer's headquarters address?", "headquarters address"),
        ("What programming language does the customer use?", "programming language"),
        ("When was the customer founded?", "founding date"),
    ]

    client = chromadb.EphemeralClient()
    collection_name = f"unknown-answer-eval-{uuid4().hex}"
    collection = client.create_collection(name=collection_name)

    try:
        chunk_embeddings = [create_embedding(chunk["text"]) for chunk in chunks]
        collection.add(
            ids=[f"chunk-{index}" for index in range(1, len(chunks) + 1)],
            documents=[chunk["text"] for chunk in chunks],
            embeddings=chunk_embeddings,
            metadatas=[
                {
                    "page": chunk["page"],
                    "source": chunk["source"],
                    "document_id": "synthetic-unknown-answer-eval",
                }
                for chunk in chunks
            ],
        )

        original_collection = vector_store.collection
        vector_store.collection = collection
        try:
            evaluation_results = []
            for question, unknown_field in evaluation_cases:
                question_embedding = create_embedding(question)
                context = vector_store.search_documents(
                    question_embedding,
                    top_k=5,
                    document_id="synthetic-unknown-answer-eval",
                )
                answer = generate_answer(question, context)
                indicated_unavailable = bool(_UNAVAILABLE_PATTERN.search(answer))
                fabricated_value = bool(
                    _UNKNOWN_VALUE_PATTERNS[unknown_field].search(answer)
                )
                passed = indicated_unavailable and not fabricated_value
                evaluation_results.append(
                    (question, answer, indicated_unavailable, fabricated_value, passed)
                )
        finally:
            vector_store.collection = original_collection

        correct = sum(passed for _, _, _, _, passed in evaluation_results)
        accuracy = correct / len(evaluation_cases)

        for question, answer, indicated_unavailable, fabricated_value, passed in evaluation_results:
            print(
                f"\nQuestion: {question}\n"
                f"Generated answer: {answer}\n"
                f"Unavailable information indicated: "
                f"{'yes' if indicated_unavailable else 'no'}\n"
                f"Obvious fabricated value detected: "
                f"{'yes' if fabricated_value else 'no'}\n"
                f"Result: {'PASS' if passed else 'FAIL'}"
            )

        print(
            "\nUnknown Answer Evaluation\n"
            "-------------------------\n"
            f"Questions: {len(evaluation_cases)}\n"
            f"Correct refusals: {correct}/{len(evaluation_cases)}\n"
            f"Accuracy: {accuracy:.0%}"
        )

        assert correct == len(evaluation_cases)
    finally:
        client.delete_collection(name=collection_name)
