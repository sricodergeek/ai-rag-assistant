from pathlib import Path

from backend.app.pdf_reader import extract_text_from_pdf


sample_pdf = Path(__file__).resolve().parents[1] / "documents" / "sample.pdf"
text = extract_text_from_pdf(str(sample_pdf))
print(text[:2000])
