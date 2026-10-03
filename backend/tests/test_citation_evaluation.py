import re
from pathlib import Path
from uuid import uuid4

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


_CITATION_PATTERN = re.compile(r"\[Source: (.*?), Page (\d+)\]")


def test_real_rag_citations_are_supported_by_retrieved_chunks():
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
    collection_name = f"citation-eval-{uuid4().hex}"
    collection = client.create_collection(name=collection_name)

    try:
        chunk_embeddings = [create_embedding(chunk) for chunk in chunks]
        collection.add(
            ids=[f"chunk-{index}" for index in range(1, len(chunks) + 1)],
            documents=chunks,
            embeddings=chunk_embeddings,
            metadatas=[
                {
                    # This one-based value is a chunk position for this text fixture,
                    # not a claimed PDF page number.
                    "page": index,
                    "source": fixture_path.name,
                    "document_id": "synthetic-citation-eval-document",
                }
                for index in range(1, len(chunks) + 1)
            ],
        )

        original_collection = vector_store.collection
        vector_store.collection = collection
        try:
            evaluation_results = []
            for question, expected_phrase in evaluation_cases:
                question_embedding = create_embedding(question)
                retrieved = vector_store.search_documents(
                    question_embedding,
                    top_k=5,
                    document_id="synthetic-citation-eval-document",
                )
                answer = generate_answer(question, retrieved)
                citations = [
                    (source, int(position))
                    for source, position in _CITATION_PATTERN.findall(answer)
                ]

                retrieved_evidence = {
                    (item["source"], item["page"]): item["text"]
                    for item in retrieved
                }
                citation_checks = [
                    {
                        "citation": citation,
                        "retrieved": citation in retrieved_evidence,
                        "contains_answer": (
                            citation in retrieved_evidence
                            and expected_phrase.casefold()
                            in retrieved_evidence[citation].casefold()
                        ),
                    }
                    for citation in citations
                ]
                passed = bool(citations) and all(
                    check["retrieved"] and check["contains_answer"]
                    for check in citation_checks
                )
                evaluation_results.append(
                    (question, expected_phrase, answer, citation_checks, passed)
                )
        finally:
            vector_store.collection = original_collection

        correct = sum(passed for _, _, _, _, passed in evaluation_results)
        accuracy = correct / len(evaluation_cases)

        for question, expected_phrase, answer, checks, passed in evaluation_results:
            print(
                f"\nQuestion: {question}\n"
                f"Generated answer: {answer}\n"
                f"Expected answer phrase: {expected_phrase}"
            )
            if not checks:
                print("Cited chunks: none (no citation found)")
            for check in checks:
                source, position = check["citation"]
                print(
                    f"Cited chunk: {source}, chunk position {position}; "
                    f"retrieved evidence: {'yes' if check['retrieved'] else 'no'}; "
                    f"contains expected phrase: "
                    f"{'yes' if check['contains_answer'] else 'no'}"
                )
            print(f"Result: {'PASS' if passed else 'FAIL'}")

        print(
            "\nCitation Evaluation\n"
            "-------------------\n"
            f"Questions: {len(evaluation_cases)}\n"
            f"Correct citations: {correct}/{len(evaluation_cases)}\n"
            f"Citation accuracy: {accuracy:.0%}"
        )

        assert correct == len(evaluation_cases)
    finally:
        client.delete_collection(name=collection_name)
