from openai import OpenAI

from backend.app.config import OPENAI_API_KEY


client = OpenAI(api_key=OPENAI_API_KEY)


def create_embedding(text: str) -> list[float]:
    """Create an embedding vector for the given text."""
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding
