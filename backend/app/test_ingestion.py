from pathlib import Path

from backend.app.ingestion import ingest_pdf


sample_pdf = Path(__file__).resolve().parents[1] / "documents" / "sample.pdf"
chunk_count = ingest_pdf(str(sample_pdf))
print(f"Chunks ingested: {chunk_count}")
