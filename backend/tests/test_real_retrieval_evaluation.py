from pathlib import Path
from uuid import uuid4

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


def test_real_retrieval_recall_at_one_three_and_five_for_synthetic_fixture():
    """Measure answer-bearing Hit@K on the synthetic Acme text fixture."""
    require_live_evaluation()
    import chromadb

    from backend.app.chunker import chunk_text
    from backend.app.embeddings import create_embedding

    vector_store = load_evaluation_vector_store()
    fixture_path = Path(__file__).parent / "fixtures" / "rag_eval_document.txt"
    document_text = fixture_path.read_text(encoding="utf-8")
    chunks = chunk_text(document_text, chunk_size=1000, chunk_overlap=200)
    chunk_positions = {chunk: index for index, chunk in enumerate(chunks, start=1)}

    evaluation_cases = [
        ("What is the customer ID?", "ACME-1042"),
        ("What subscription plan does the customer have?", "Enterprise"),
        ("How much is the monthly subscription?", "$2,499"),
        ("When does the contract renew?", "June 30, 2027"),
        ("What is the support response SLA?", "2 hours"),
        ("How long is data retained?", "90 days"),
    ]

    client = chromadb.EphemeralClient()
    collection_name = f"retrieval-eval-{uuid4().hex}"
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
                    "document_id": "synthetic-eval-document",
                }
                for _ in chunks
            ],
        )

        retrieved_results_by_question = []
        original_collection = vector_store.collection
        vector_store.collection = collection
        try:
            for question, _ in evaluation_cases:
                question_embedding = create_embedding(question)
                results = vector_store.search_documents(
                    question_embedding,
                    top_k=5,
                    document_id="synthetic-eval-document",
                )
                retrieved_results_by_question.append(results)
        finally:
            vector_store.collection = original_collection

        answer_matches_by_question = [
            [
                (rank, chunk_positions[result["text"]])
                for rank, result in enumerate(results, start=1)
                if answer_phrase in result["text"]
            ]
            for (_, answer_phrase), results in zip(
                evaluation_cases, retrieved_results_by_question
            )
        ]
        hits = {
            cutoff: sum(
                any(rank <= cutoff for rank, _ in matches)
                for matches in answer_matches_by_question
            )
            for cutoff in (1, 3, 5)
        }
        hit_at_5 = hits[5] / len(evaluation_cases)

        print("\n## Retrieval Evaluation")
        print(f"Questions: {len(evaluation_cases)}")
        for cutoff, successes in hits.items():
            percentage = successes / len(evaluation_cases)
            print(
                f"Hit@{cutoff}: {successes}/{len(evaluation_cases)} "
                f"({percentage:.0%})"
            )
        for (question, answer_phrase), results, matches in zip(
            evaluation_cases,
            retrieved_results_by_question,
            answer_matches_by_question,
        ):
            retrieved_ranking = [
                chunk_positions[result["text"]] for result in results
            ]
            matching_chunks = ", ".join(
                f"chunk {chunk_number} at rank {rank}"
                for rank, chunk_number in matches
            ) or "none"
            print(
                f"- {question}\n"
                f"  Expected answer: {answer_phrase}\n"
                f"  Retrieved ranking (chunk numbers): {retrieved_ranking}\n"
                f"  Matching answer chunk(s): {matching_chunks}"
            )

        assert hit_at_5 == 1.0
    finally:
        client.delete_collection(name=collection_name)
