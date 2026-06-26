"""Resume ingestion pipeline.

Six steps, each yielding a progress dict so callers can stream them over SSE:

1. Parse PDF (pypdf) or DOCX (python-docx) -> raw text
2. GPT-4o structured extraction -> canonical JSON
3. Persist to SQLite ``resume_profiles``
4. Chunk (512 tok / 50 overlap) + embed -> ChromaDB ``resume:{user_id}``
5. Regenerate ATS master PDF (WeasyPrint + Jinja2)
6. Extract semantic career facts -> ChromaDB ``memory:{user_id}``
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, AsyncIterator

from agent.llm import complete_json
from agent.resume.pdf_generator import generate_ats_pdf
from db import chroma, sqlite

RESUMES_DIR = Path(os.getenv("RESUMES_DIR", "resumes"))

# ~4 chars/token heuristic -> 512 tokens ≈ 2048 chars, 50 token overlap ≈ 200 chars
CHUNK_CHARS = 2048
CHUNK_OVERLAP_CHARS = 200


_EXTRACTION_SYSTEM = """You are a resume parsing engine. Extract the candidate's \
information from the raw resume text into a strict canonical JSON object with \
exactly these top-level keys:

{
  "contact": {"name": "", "email": "", "phone": "", "location": "",
              "linkedin": "", "website": ""},
  "summary": "",
  "skills": ["..."],
  "experience": [
    {"company": "", "title": "", "location": "", "start": "", "end": "",
     "bullets": ["..."]}
  ],
  "education": [
    {"institution": "", "degree": "", "field": "", "start": "", "end": "",
     "details": ""}
  ],
  "certifications": ["..."],
  "projects": [{"name": "", "description": "", "bullets": ["..."]}]
}

Use empty strings / empty arrays where information is missing. Never invent data.
Return ONLY the JSON object."""


_FACTS_SYSTEM = """You extract durable career facts from a resume profile. \
Return a JSON object: {"facts": ["fact 1", "fact 2", ...]}. Each fact must be a \
short, self-contained sentence capturing skills, seniority, domains, notable \
achievements, or stated preferences. Produce 5-15 facts."""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_document(filename: str, raw_bytes: bytes) -> str:
    """Extract plain text from PDF or DOCX bytes."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(raw_bytes)
    if suffix in (".docx", ".doc"):
        return _parse_docx(raw_bytes)
    # Fall back to UTF-8 text.
    return raw_bytes.decode("utf-8", errors="ignore")


def _parse_pdf(raw_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_docx(raw_bytes: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(raw_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def chunk_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    step = max(1, CHUNK_CHARS - CHUNK_OVERLAP_CHARS)
    while start < len(text):
        chunks.append(text[start : start + CHUNK_CHARS])
        start += step
    return chunks


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
async def ingest(
    user_id: str, filename: str, raw_bytes: bytes
) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding progress events; final event carries the profile."""
    # Step 1 — parse ------------------------------------------------------- #
    yield {"type": "progress", "step": "Parsing document"}
    raw_text = parse_document(filename, raw_bytes)
    if not raw_text.strip():
        yield {"type": "error", "message": "Could not extract text from document."}
        return

    # Step 2 — structured extraction -------------------------------------- #
    yield {"type": "progress", "step": "Extracting structured profile (GPT-4o)"}
    profile = await complete_json(_EXTRACTION_SYSTEM, raw_text[:24000])
    if not profile:
        yield {"type": "error", "message": "Structured extraction failed."}
        return

    # Step 3 — persist to SQLite ------------------------------------------ #
    yield {"type": "progress", "step": "Persisting profile"}
    record = sqlite.create_resume_profile(user_id, filename, profile)

    # Step 4 — chunk + embed into resume namespace ----------------------- #
    yield {"type": "progress", "step": "Embedding resume chunks"}
    ns = chroma.resume_ns(user_id)
    chroma.delete_namespace(ns)
    chunks = chunk_text(raw_text)
    if chunks:
        chroma.upsert(
            ns,
            documents=chunks,
            metadatas=[
                {"version": record["version"], "chunk": i}
                for i in range(len(chunks))
            ],
        )

    # Step 5 — regenerate ATS master PDF ---------------------------------- #
    yield {"type": "progress", "step": "Generating ATS master PDF"}
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = RESUMES_DIR / f"{user_id}_master_v{record['version']}.pdf"
    try:
        generate_ats_pdf(profile, pdf_path)
        sqlite.update_application  # noqa: B018 - keep import graph stable
        _set_master_pdf(record["id"], str(pdf_path))
        record["master_pdf_path"] = str(pdf_path)
    except Exception as exc:  # PDF generation depends on system libs (cairo/pango)
        yield {
            "type": "progress",
            "step": f"PDF generation skipped: {exc}",
        }

    # Step 6 — extract semantic career facts ------------------------------ #
    yield {"type": "progress", "step": "Extracting career facts"}
    facts_obj = await complete_json(
        _FACTS_SYSTEM, _profile_for_facts(profile)
    )
    facts = facts_obj.get("facts", []) if isinstance(facts_obj, dict) else []
    if facts:
        mem_ns = chroma.memory_ns(user_id)
        chroma.upsert(
            mem_ns,
            documents=facts,
            metadatas=[{"source": "resume", "version": record["version"]} for _ in facts],
        )

    yield {
        "type": "done",
        "profile": record,
        "facts": facts,
        "chunks": len(chunks),
    }


def _set_master_pdf(profile_id: int, path: str) -> None:
    with sqlite.get_conn() as conn:
        conn.execute(
            "UPDATE resume_profiles SET master_pdf_path = ? WHERE id = ?",
            (path, profile_id),
        )


def _profile_for_facts(profile: dict[str, Any]) -> str:
    import json

    return json.dumps(profile)[:16000]
