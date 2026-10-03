from backend.app.embeddings import create_embedding
from backend.app.llm import generate_answer
from backend.app.vector_store import search_documents


question = "What does the document say about HDL cholesterol and Vitamin D?"
embedding = create_embedding(question)
documents = search_documents(embedding, top_k=3)
answer = generate_answer(question, documents)

print(f"Question: {question}")
print(f"Retrieved documents: {len(documents)}")
print(f"Answer: {answer}")
