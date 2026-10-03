from pathlib import Path

import chromadb


_CHROMA_PATH = Path(__file__).resolve().parents[1] / "chroma_db"
client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
collection = client.get_or_create_collection(name="documents")


def reset_collection() -> None:
    """Delete and recreate the documents collection."""
    global collection
    client.delete_collection(name="documents")
    collection = client.create_collection(name="documents")


def delete_document_vectors(document_id: str) -> None:
    """Delete Chroma chunks belonging to exactly one document ID."""
    collection.delete(where={"document_id": str(document_id)})


def add_document(
    chunk_id: str,
    document_id: str,
    text: str,
    embedding: list[float],
    page: int,
    source: str,
) -> None:
    """Store a document chunk, its embedding, and document metadata."""
    collection.add(
        ids=[chunk_id],
        documents=[text],
        embeddings=[embedding],
        metadatas=[{"page": page, "source": source, "document_id": document_id}],
    )


def search_documents(
    embedding: list[float],
    top_k: int = 3,
    document_id: str | None = None,
    max_distance: float | None = None,
) -> list[dict]:
    """Return matching document texts with their page and source metadata."""
    where = {"document_id": document_id} if document_id is not None else None
    results = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    documents = (results.get("documents") or [[]])[0] or []
    metadatas = (results.get("metadatas") or [[]])[0] or []
    distances = (results.get("distances") or [[]])[0] or []

    matches = []
    for index, text in enumerate(documents):
        if index >= len(metadatas):
            continue

        distance = distances[index] if index < len(distances) else None
        if max_distance is not None and (
            distance is None or distance > max_distance
        ):
            continue

        metadata = metadatas[index]
        matches.append(
            {
                "text": text,
                "page": metadata["page"],
                "source": metadata["source"],
                "distance": distance,
            }
        )

    return matches
