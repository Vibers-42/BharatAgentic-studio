"""Extracts text from a PDF, one entry per page, so every chunk can cite its page number.
Scanned PDFs (images only) have no text layer and come back empty; OCR is future work."""

from pypdf import PdfReader


def extract_pages(path: str) -> list[tuple[int, str]]:
    """Return [(page_number starting at 1, text), ...], skipping pages with no text."""
    reader = PdfReader(path)
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((number, text))
    return pages
