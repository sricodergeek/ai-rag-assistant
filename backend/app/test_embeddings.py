from backend.app.embeddings import create_embedding


embedding = create_embedding(
    "This document contains information about cholesterol and blood tests."
)

print(len(embedding))
print(embedding[:5])
