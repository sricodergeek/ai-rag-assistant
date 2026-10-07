from openai import OpenAI

from backend.app.config import OPENAI_API_KEY


client = OpenAI(api_key=OPENAI_API_KEY)


def generate_answer(
    question: str,
    context: list[dict],
    history: list[dict] | None = None,
) -> str:
    """Answer a question using only the supplied document context."""
    context_text = "\n\n".join(
        f"[Source: {chunk['source']}, Page {chunk['page']}]\n{chunk['text']}"
        for chunk in context
    )
    history_text = "\n".join(
        f"{message['role'].capitalize()}: {message['content']}"
        for message in (history or [])
    ) or "(no previous messages)"
    response = client.responses.create(
        model="gpt-5.6-luna",
        instructions=(
            "Answer using only the supplied context. If the context does not "
            "contain the answer, say that the information is not available in "
            "the provided document. When using information from a retrieved "
            "chunk, cite it in exactly this format: [Source: <source>, Page "
            "<page>]. Use only the source and page labels supplied with the "
            "context; do not invent citations. Use conversation history only "
            "to understand references and follow-up questions. History is not "
            "a source of factual information; all factual answers must be "
            "grounded in the supplied document context. Answer in the same "
            "language as the user's current question: Telugu for Telugu, Hindi "
            "for Hindi, and English for English. Do not translate the question "
            "into English except if needed for internal reasoning. Preserve "
            "source labels and the required citation format exactly."
        ),
        input=(
            f"Conversation history:\n{history_text}\n\n"
            f"Document context:\n{context_text}\n\nQuestion:\n{question}"
        ),
    )
    return response.output_text
