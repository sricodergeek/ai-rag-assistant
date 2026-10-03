from backend.app.embeddings import create_embedding
from backend.app.vector_store import search_documents


question = "What does the document say about HDL cholesterol and Vitamin D?"
embedding = create_embedding(question)
documents = search_documents(embedding, top_k=3)

print(f"Retrieved documents: {len(documents)}")
for number, document in enumerate(documents, start=1):
    print(f"Document {number}:")
    print(f"Source: {document['source']}")
    print(f"Page: {document['page']}")
    print(f"Text: {document['text'][:200]}")
