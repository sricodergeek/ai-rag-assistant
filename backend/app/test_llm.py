from backend.app.llm import generate_answer


def test_generate_answer_returns_an_answer_from_document_context():
    question = "What is cholesterol measured using?"
    context = [
        {
            "text": "Cholesterol is measured using blood tests.",
            "page": 1,
            "source": "sample.pdf",
        }
    ]

    answer = generate_answer(question, context)
    print(answer)

    assert isinstance(answer, str)
    assert answer.strip()
