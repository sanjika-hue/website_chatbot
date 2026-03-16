"""
OCR / document-to-markdown converter.

Current backend: docling
Can be swapped with a VLM-based approach (e.g. vLLM + vision model) later.
"""

import logging
import tempfile
from pathlib import Path

log = logging.getLogger("tastebud.ocr")

# Lazy-load the converter to avoid slow startup
_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        log.info("Loading document converter (first call)…")
        from docling.document_converter import DocumentConverter
        _converter = DocumentConverter()
        log.info("Document converter ready.")
    return _converter


def _extract_pdf_page(file_bytes: bytes, page: int) -> bytes:
    """Extract a single page from a PDF, return new PDF bytes."""
    import fitz  # PyMuPDF

    src = fitz.open(stream=file_bytes, filetype="pdf")
    if page < 1 or page > len(src):
        src.close()
        raise ValueError(f"Page {page} out of range (document has {len(src)} pages)")

    dst = fitz.open()
    dst.insert_pdf(src, from_page=page - 1, to_page=page - 1)
    out = dst.tobytes()
    dst.close()
    src.close()
    return out


def get_pdf_page_count(file_bytes: bytes) -> int:
    """Return the number of pages in a PDF."""
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    count = len(doc)
    doc.close()
    return count


def convert_document(file_bytes: bytes, filename: str, page: int | None = None) -> str:
    """Convert a document to markdown. Returns the markdown string.

    For PDFs, if `page` is given, only that page is converted.
    """
    suffix = Path(filename).suffix.lower()

    # For PDFs, extract single page
    if suffix == ".pdf" and page is not None:
        file_bytes = _extract_pdf_page(file_bytes, page)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        converter = _get_converter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        Path(tmp_path).unlink(missing_ok=True)
