from pypdf import PdfReader


def extract_text_from_pdf(file_path: str) -> str:
    """Extract and concatenate the text from every page in a PDF file.

    Pages whose text extraction returns ``None`` are treated as empty pages.

    Args:
        file_path: Path to the PDF file to read.

    Returns:
        The extracted page text concatenated in document order.
    """
    reader = PdfReader(file_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)
