"""Document text extraction (PDF, DOCX, plain text). Single responsibility: parse bytes -> str."""

from __future__ import annotations

import io
from pathlib import Path


def parse_document(filename: str, raw_bytes: bytes) -> str:
    """Extract plain text from PDF, DOCX, or plain text bytes."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(raw_bytes)
    if suffix in (".docx", ".doc"):
        return _parse_docx(raw_bytes)
    return raw_bytes.decode("utf-8", errors="ignore")


def _parse_pdf(raw_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_docx(raw_bytes: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(raw_bytes))
    return "\n".join(p.text for p in doc.paragraphs)
