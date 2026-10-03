from unittest.mock import patch

from backend.tests.evaluation_harness import load_evaluation_vector_store


def test_recall_at_five_for_synthetic_questions():
    """Check page-presence regression behavior using predetermined query results."""
    vector_store = load_evaluation_vector_store()
    evaluation_cases = [
        (
            "What was the HbA1c?",
            2,
            [
                ("Fasting glucose was 96 mg/dL.", 1),
                ("HbA1c was 5.8 percent.", 2),
                ("Creatinine was 0.9 mg/dL.", 2),
                ("The patient reported no symptoms.", 3),
                ("Hemoglobin was 14.1 g/dL.", 1),
            ],
        ),
        (
            "What was the TSH?",
            3,
            [
                ("Vitamin D was 32 ng/mL.", 4),
                ("Free T4 was within range.", 3),
                ("TSH was 2.1 mIU/L.", 3),
                ("Total cholesterol was 184 mg/dL.", 2),
                ("The sample was collected in the morning.", 1),
            ],
        ),
        (
            "What was the LDL?",
            2,
            [
                ("HDL cholesterol was 58 mg/dL.", 2),
                ("Triglycerides were 110 mg/dL.", 2),
                ("LDL cholesterol was 108 mg/dL.", 2),
                ("Vitamin B12 was 420 pg/mL.", 4),
                ("The lipid panel was reviewed by the clinician.", 3),
            ],
        ),
    ]

    query_results = [
        {
            "documents": [[text for text, _ in chunks]],
            "metadatas": [
                [
                    {
                        "page": page,
                        "source": "synthetic_lab_report.pdf",
                        "document_id": "synthetic-document",
                    }
                    for _, page in chunks
                ]
            ],
            "distances": [[0.1, 0.2, 0.3, 0.4, 0.5]],
        }
        for _, _, chunks in evaluation_cases
    ]

    successful_retrievals = 0
    with patch.object(vector_store, "collection") as mock_collection:
        mock_collection.query.side_effect = query_results

        for index, (question, expected_page, _) in enumerate(evaluation_cases):
            results = vector_store.search_documents(
                embedding=[float(index)],
                top_k=5,
            )
            if any(result["page"] == expected_page for result in results):
                successful_retrievals += 1

    page_presence_rate = successful_retrievals / len(evaluation_cases)
    print(
        f"Page-presence regression check: {page_presence_rate:.1f} "
        f"({successful_retrievals}/{len(evaluation_cases)} questions)"
    )
    assert page_presence_rate == 1.0
