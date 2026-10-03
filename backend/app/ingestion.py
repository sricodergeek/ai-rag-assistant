import uuid

from backend.app.chunker import chunk_pdf
from backend.app.embeddings import create_embedding
from backend.app.vector_store import add_document


def ingest_pdf(
    file_path: str,
    document_id: str | None = None,
) -> tuple[int, str]:
    """Extract, chunk, embed, and store the text from a PDF file.

    Args:
        file_path: Path to the PDF file.
        document_id: Optional ID to use for this document's Chroma records.

    Returns:
        A tuple containing the number of chunks stored and the unique
        ingestion ID assigned to the PDF.
    """
    ingestion_id = document_id or str(uuid.uuid4())
    chunks = chunk_pdf(file_path)

    for index, chunk in enumerate(chunks):
        embedding = create_embedding(chunk["text"])
        add_document(
            chunk_id=f"{ingestion_id}-{index}",
            document_id=ingestion_id,
            text=chunk["text"],
            embedding=embedding,
            page=chunk["page"],
            source=chunk["source"],
        )

    return len(chunks), ingestion_id
