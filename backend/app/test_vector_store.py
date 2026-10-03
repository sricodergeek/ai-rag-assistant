import uuid

from backend.app.embeddings import create_embedding
from backend.app.vector_store import add_document, search_documents


def test_added_document_can_be_retrieved():
    document_text = "Cholesterol is measured using blood tests."
    document_embedding = create_embedding(document_text)
    document_id = f"test-{uuid.uuid4()}"
    add_document(
        chunk_id=f"{document_id}-0",
        document_id=document_id,
        text=document_text,
        embedding=document_embedding,
        page=1,
        source="sample.pdf",
    )

    query_embedding = create_embedding("How is cholesterol measured?")
    matching_documents = search_documents(query_embedding, document_id=document_id)
    print(matching_documents)

    assert any(
        document["text"] == document_text
        and document["page"] == 1
        and document["source"] == "sample.pdf"
        for document in matching_documents
    )
