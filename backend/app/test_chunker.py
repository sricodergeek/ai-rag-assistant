from pathlib import Path

from backend.app.chunker import chunk_pdf


sample_pdf = Path(__file__).resolve().parents[1] / "documents" / "sample.pdf"
chunks = chunk_pdf(str(sample_pdf))

print(f"Total chunks: {len(chunks)}")
for number, chunk in enumerate(chunks[:3], start=1):
    print(f"Chunk {number}:")
    print(f"Source: {chunk['source']}")
    print(f"Page: {chunk['page']}")
    print(f"Text length: {len(chunk['text'])}")
    print(f"Text: {chunk['text'][:200]}")
