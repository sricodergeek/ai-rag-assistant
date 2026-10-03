from pathlib import Path
from uuid import uuid4

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


def test_real_rag_answers_for_synthetic_fixture():
    require_live_evaluation()
    import chromadb

    from backend.app.chunker import chunk_text
    from backend.app.embeddings import create_embedding
    from backend.app.llm import generate_answer

    vector_store = load_evaluation_vector_store()
    fixture_path = Path(__file__).parent / "fixtures" / "rag_eval_document.txt"
    document_text = fixture_path.read_text(encoding="utf-8")
    chunks = chunk_text(document_text, chunk_size=1000, chunk_overlap=200)

    evaluation_cases = [
        ("What is the customer ID?", "ACME-1042"),
        ("What subscription plan does the customer have?", "Enterprise"),
        ("How much is the monthly subscription?", "$2,499"),
        ("When does the contract renew?", "June 30, 2027"),
        ("What is the support response SLA?", "2 hours"),
        ("How long is data retained?", "90 days"),
    ]

    client = chromadb.EphemeralClient()
    collection_name = f"answer-eval-{uuid4().hex}"
    collection = client.create_collection(name=collection_name)

    try:
        chunk_embeddings = [create_embedding(chunk) for chunk in chunks]
        collection.add(
            ids=[f"chunk-{index}" for index in range(1, len(chunks) + 1)],
            documents=chunks,
            embeddings=chunk_embeddings,
            metadatas=[
                {
                    "page": 1,
                    "source": fixture_path.name,
                    "document_id": "synthetic-answer-eval-document",
                }
                for _ in chunks
            ],
        )

        original_collection = vector_store.collection
        vector_store.collection = collection
        try:
            results = []
            for question, expected_answer in evaluation_cases:
                question_embedding = create_embedding(question)
                context = vector_store.search_documents(
                    question_embedding,
                    top_k=5,
                    document_id="synthetic-answer-eval-document",
                )
                answer = generate_answer(question, context)
                passed = expected_answer.casefold() in answer.casefold()
                results.append((question, expected_answer, answer, passed))
        finally:
            vector_store.collection = original_collection

        correct = sum(passed for _, _, _, passed in results)
        accuracy = correct / len(evaluation_cases)

        for question, expected_answer, answer, passed in results:
            print(
                f"\nQuestion: {question}\n"
                f"Expected answer: {expected_answer}\n"
                f"Generated answer: {answer}\n"
                f"Result: {'PASS' if passed else 'FAIL'}"
            )

        print(
            "\nAnswer Evaluation\n"
            "-----------------\n"
            f"Questions: {len(evaluation_cases)}\n"
            f"Correct: {correct}/{len(evaluation_cases)}\n"
            f"Accuracy: {accuracy:.0%}"
        )

        assert correct == len(evaluation_cases)
    finally:
        client.delete_collection(name=collection_name)
