"""Resume ingestion pipeline — orchestrates parse → extract → persist → embed → PDF → facts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from agent.resume.embedder import embed_career_facts, embed_resume_chunks
from agent.resume.extractor import extract_facts, extract_profile
from agent.resume.parser import parse_document
from agent.resume.pdf_generator import generate_ats_pdf
from db import sqlite

RESUMES_DIR = Path(os.getenv("RESUMES_DIR", "resumes"))


async def ingest(
    user_id: str, filename: str, raw_bytes: bytes
) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding progress events; final event carries the profile."""
    yield {"type": "progress", "step": "Parsing document"}
    raw_text = parse_document(filename, raw_bytes)
    if not raw_text.strip():
        yield {"type": "error", "message": "Could not extract text from document."}
        return

    yield {"type": "progress", "step": "Extracting structured profile"}
    profile = await extract_profile(raw_text)
    if not profile:
        yield {"type": "error", "message": "Structured extraction failed."}
        return

    yield {"type": "progress", "step": "Persisting profile"}
    record = sqlite.create_resume_profile(user_id, filename, profile)

    yield {"type": "progress", "step": "Embedding resume chunks"}
    chunk_count = embed_resume_chunks(user_id, raw_text, record["version"])

    yield {"type": "progress", "step": "Generating ATS master PDF"}
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    _delete_old_pdfs(user_id, current_version=record["version"])
    pdf_path = RESUMES_DIR / f"{user_id}_master_v{record['version']}.pdf"
    try:
        generate_ats_pdf(profile, pdf_path)
        _set_master_pdf(record["id"], str(pdf_path))
        record["master_pdf_path"] = str(pdf_path)
    except Exception as exc:
        yield {"type": "progress", "step": f"PDF generation skipped: {exc}"}

    yield {"type": "progress", "step": "Extracting career facts"}
    facts = await extract_facts(profile)
    embed_career_facts(user_id, facts, record["version"])

    yield {"type": "done", "profile": record, "facts": facts, "chunks": chunk_count}


def _set_master_pdf(profile_id: int, path: str) -> None:
    with sqlite.get_conn() as conn:
        conn.execute(
            "UPDATE resume_profiles SET master_pdf_path = ? WHERE id = ?",
            (path, profile_id),
        )


def _delete_old_pdfs(user_id: str, current_version: int) -> None:
    """Delete PDF files from previous versions to reclaim disk space."""
    for pdf in RESUMES_DIR.glob(f"{user_id}_master_v*.pdf"):
        version_str = pdf.stem.split("_master_v")[-1]
        try:
            if int(version_str) < current_version:
                pdf.unlink(missing_ok=True)
        except ValueError:
            pass
