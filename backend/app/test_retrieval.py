from backend.app.embeddings import create_embedding
from backend.app.vector_store import search_documents


question = "What does the document say about LDL cholesterol?"
embedding = create_embedding(question)
documents = search_documents(embedding, top_k=3)

for number, document in enumerate(documents, start=1):
    print(f"{number}. {document}")
