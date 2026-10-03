"""Utilities for splitting document text into chunks for RAG workflows."""

from pathlib import Path

from pypdf import PdfReader


def chunk_text(
    text: str, chunk_size: int = 1000, chunk_overlap: int = 200
) -> list[str]:
    """Split text into overlapping character chunks.

    Args:
        text: The text to split.
        chunk_size: Approximate maximum number of characters per chunk.
        chunk_overlap: Number of characters shared by consecutive chunks.

    Returns:
        Non-empty text chunks in their original order.

    Raises:
        ValueError: If ``chunk_size`` is not positive or ``chunk_overlap`` is
            negative or greater than or equal to ``chunk_size``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    step = chunk_size - chunk_overlap
    return [
        chunk
        for start in range(0, len(text), step)
        if (chunk := text[start : start + chunk_size])
    ]


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text near word boundaries while keeping chunks within the target size."""
    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end + 1)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        next_start = max(start + 1, end - chunk_overlap)
        # Move the next start to a word boundary where possible, retaining
        # approximately the requested number of overlapping characters.
        boundary = text.find(" ", next_start, end)
        if boundary != -1:
            next_start = boundary + 1
        start = next_start

    return chunks


def chunk_pdf(
    file_path: str, chunk_size: int = 1000, chunk_overlap: int = 200
) -> list[dict]:
    """Extract and split a PDF into page-attributed text chunks.

    Each result is a dictionary with exactly ``text`` (the chunk text),
    ``page`` (a 1-based page number), and ``source`` (the PDF filename).
    Text from separate pages is always chunked independently.

    Args:
        file_path: Path to the PDF file.
        chunk_size: Maximum target size, in characters, for each chunk.
        chunk_overlap: Approximate number of shared characters between
            consecutive chunks from the same page.

    Returns:
        Page-attributed text chunk dictionaries in document order.

    Raises:
        ValueError: If ``chunk_size`` is not positive, or if
            ``chunk_overlap`` is negative or greater than or equal to
            ``chunk_size``.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be greater than or equal to 0")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    source = Path(file_path).name
    reader = PdfReader(file_path)
    result: list[dict] = []

    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text()
        if not page_text:
            continue

        for chunk in _split_text(page_text, chunk_size, chunk_overlap):
            result.append(
                {"text": chunk, "page": page_number, "source": source}
            )

    return result
