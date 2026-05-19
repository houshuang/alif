"""PDF text extraction via PyMuPDF (fitz).

Page-at-a-time so big textbooks don't load fully into memory and we can
process lazily. PyMuPDF handles Greek diacritics + Latin macrons natively.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ExtractedPage:
    page_number: int                # 1-indexed
    text: str


def extract_pages(pdf_path: str | Path) -> list[ExtractedPage]:
    """Extract all pages from a PDF. Pages with no extractable text become
    empty strings (kept so page numbers stay aligned with the source PDF).
    """
    import fitz  # PyMuPDF
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(pdf_path)

    out: list[ExtractedPage] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text") or ""
            except Exception as e:
                log.warning("Failed to extract page %d of %s: %s", i, pdf_path, e)
                text = ""
            out.append(ExtractedPage(page_number=i, text=text.strip()))
    return out


def count_pages(pdf_path: str | Path) -> int:
    import fitz
    with fitz.open(pdf_path) as doc:
        return doc.page_count
