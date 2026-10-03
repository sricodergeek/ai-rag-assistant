import re
from pathlib import Path
from uuid import uuid4

import pytest

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


_CITATION_PATTERN = re.compile(r"\[Source: (.*?), Page (\d+)\]")


def _score_pdf_citations(answer, retrieved, expected_phrase, expected_page):
    """Check benchmark evidence in retrieved chunks for every source/page citation."""
    citations = [
        (source, int(page)) for source, page in _CITATION_PATTERN.findall(answer)
    ]
    supporting_pages = [
        citation
        for citation in citations
        if any(
            (chunk["source"], chunk["page"]) == citation
            and expected_phrase.casefold() in chunk["text"].casefold()
            for chunk in retrieved
        )
    ]
    return {
        "citations": citations,
        "retrieved_expected_page": any(
            chunk["page"] == expected_page for chunk in retrieved
        ),
        "supporting_pages": supporting_pages,
        "passed": (
            expected_phrase.casefold() in answer.casefold()
            and bool(citations)
            and len(supporting_pages) == len(citations)
        ),
    }


def test_real_pdf_citations_reference_retrieved_supporting_pages():
    require_live_evaluation()
    # Keep service clients and persistent Chroma initialization out of offline tests.
    import chromadb

    from backend.app.chunker import chunk_pdf
    from backend.app.embeddings import create_embedding
    from backend.app.llm import generate_answer

    vector_store = load_evaluation_vector_store()

    pdf_path = Path(__file__).parent / "fixtures" / "rag_eval_document.pdf"
    chunks = chunk_pdf(str(pdf_path))

    evaluation_cases = [
        ("What is the customer ID?", "ACME-1042", 1),
        ("What subscription plan does the customer have?", "Enterprise", 2),
        ("How much is the monthly subscription?", "$2,499", 2),
        ("When does the contract renew?", "June 30, 2027", 2),
        ("What is the support response SLA?", "2 hours", 3),
        ("How long is data retained?", "90 days", 4),
    ]

    client = chromadb.EphemeralClient()
    collection_name = f"pdf-citation-eval-{uuid4().hex}"
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
                    "document_id": "synthetic-pdf-citation-eval",
                }
                for chunk in chunks
            ],
        )

        original_collection = vector_store.collection
        vector_store.collection = collection
        try:
            evaluation_results = []
            for question, expected_phrase, expected_page in evaluation_cases:
                question_embedding = create_embedding(question)
                retrieved = vector_store.search_documents(
                    question_embedding,
                    top_k=5,
                    document_id="synthetic-pdf-citation-eval",
                )
                answer = generate_answer(question, retrieved)
                scoring = _score_pdf_citations(
                    answer, retrieved, expected_phrase, expected_page
                )
                evaluation_results.append(
                    {
                        "question": question,
                        "expected_phrase": expected_phrase,
                        "expected_page": expected_page,
                        "answer": answer,
                        **scoring,
                    }
                )
        finally:
            vector_store.collection = original_collection

        correct = sum(result["passed"] for result in evaluation_results)
        accuracy = correct / len(evaluation_cases)

        for result in evaluation_results:
            citations_display = [
                f"[Source: {source}, Page {page}]"
                for source, page in result["citations"]
            ]
            supporting_display = sorted(
                {page for _, page in result["supporting_pages"]}
            )
            print(
                f"\nQuestion: {result['question']}\n"
                f"Expected answer: {result['expected_phrase']}\n"
                f"Generated answer: {result['answer']}\n"
                f"Citations: {citations_display or 'none'}\n"
                f"Supporting page(s): {supporting_display or 'none'}\n"
                f"Expected page {result['expected_page']} in top 5: "
                f"{'yes' if result['retrieved_expected_page'] else 'no'}\n"
                f"Result: {'PASS' if result['passed'] else 'FAIL'}"
            )

        print(
            "\nPDF Citation Evaluation\n"
            "----------------------\n"
            f"Questions: {len(evaluation_cases)}\n"
            f"Correct citations: {correct}/{len(evaluation_cases)}\n"
            f"Citation accuracy: {accuracy:.0%}"
        )

        assert correct == len(evaluation_cases)
    finally:
        client.delete_collection(name=collection_name)


@pytest.mark.parametrize(
    "answer, extra_chunks, expected_pass",
    [
        ("ACME-1042 [Source: rag_eval_document.pdf, Page 1]", [], True),
        ("ACME-1042 [Source: rag_eval_document.pdf, Page 1] "
         "[Source: rag_eval_document.pdf, Page 2]", [], False),
        ("ACME-1042 [Source: rag_eval_document.pdf, Page 1] "
         "[Source: other.pdf, Page 1]", [], False),
        ("ACME-1042 [Source: rag_eval_document.pdf, Page 1] "
         "[Source: rag_eval_document.pdf, Page 2]",
         [{"source": "rag_eval_document.pdf", "page": 2, "text": "Enterprise plan."}],
         False),
        ("ACME-1042", [], False),
        ("Unknown [Source: rag_eval_document.pdf, Page 1]", [], False),
    ],
    ids=["valid", "extra-unretrieved-page", "extra-unretrieved-source",
         "extra-retrieved-page-without-evidence", "no-citation", "wrong-answer"],
)
def test_offline_pdf_citation_scoring(answer, extra_chunks, expected_pass):
    retrieved = [
        {"source": "rag_eval_document.pdf", "page": 1, "text": "Customer ID: ACME-1042"},
        *extra_chunks,
    ]
    result = _score_pdf_citations(answer, retrieved, "ACME-1042", 1)
    assert result["passed"] is expected_pass
    assert result["retrieved_expected_page"] is True


def test_offline_pdf_citation_scoring_accepts_multiple_supported_citations():
    retrieved = [
        {"source": "synthetic.pdf", "page": 1, "text": "Customer ID: ACME-1042"},
        {"source": "synthetic.pdf", "page": 2, "text": "Account summary for ACME-1042."},
    ]
    answer = (
        "The customer ID is ACME-1042. "
        "[Source: synthetic.pdf, Page 1] [Source: synthetic.pdf, Page 2]"
    )

    result = _score_pdf_citations(answer, retrieved, "ACME-1042", 1)

    assert result["citations"] == [("synthetic.pdf", 1), ("synthetic.pdf", 2)]
    assert result["supporting_pages"] == result["citations"]
    assert result["retrieved_expected_page"] is True
    assert result["passed"] is True


@pytest.mark.parametrize("same_page_retrieved", [False, True])
def test_offline_pdf_citation_requires_retrieved_fixture_evidence(same_page_retrieved):
    from backend.app.chunker import chunk_pdf

    chunks = chunk_pdf(str(Path(__file__).parent / "fixtures" / "rag_eval_document.pdf"))
    phrase = "2 hours"
    assert any(chunk["page"] == 3 and phrase in chunk["text"] for chunk in chunks)
    retrieved = [
        chunk for chunk in chunks
        if (chunk["page"] == 3 if same_page_retrieved else chunk["page"] != 3)
        and phrase not in chunk["text"]
    ]
    assert retrieved
    result = _score_pdf_citations(
        "The SLA is 2 hours. [Source: rag_eval_document.pdf, Page 3]",
        retrieved, phrase, 3,
    )
    assert result["retrieved_expected_page"] is same_page_retrieved
    assert result["supporting_pages"] == []
    assert result["passed"] is False
