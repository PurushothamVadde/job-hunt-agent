"""Career-document upload endpoint.

Writes directly to ChromaDB ``docs:{user_id}`` (the RAG tool is a query-time
consumer only). Parses PDF/DOCX/TXT, chunks, embeds and upserts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile

from agent.resume.ingestion import chunk_text, parse_document
from api.auth import get_current_user
from db import chroma

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
):
    raw = await file.read()
    filename = file.filename or "document.txt"
    text = parse_document(filename, raw)
    chunks = chunk_text(text)

    ns = chroma.docs_ns(user["user_id"])
    ids = chroma.upsert(
        ns,
        documents=chunks,
        metadatas=[{"filename": filename, "chunk": i} for i in range(len(chunks))],
    )
    return {
        "filename": filename,
        "chunks_indexed": len(ids),
        "namespace": ns,
    }
