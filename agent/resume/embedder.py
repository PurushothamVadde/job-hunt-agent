"""Text chunking and ChromaDB embedding. Single responsibility: text -> vector store."""

from __future__ import annotations

from db import chroma

# ~4 chars/token heuristic -> 512 tokens ≈ 2048 chars, 50 token overlap ≈ 200 chars
CHUNK_CHARS = 2048
CHUNK_OVERLAP_CHARS = 200


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


def embed_resume_chunks(user_id: str, raw_text: str, version: int) -> int:
    """Chunk raw text and upsert into the resume namespace. Returns chunk count."""
    ns = chroma.resume_ns(user_id)
    chroma.delete_namespace(ns)
    chunks = chunk_text(raw_text)
    if chunks:
        chroma.upsert(
            ns,
            documents=chunks,
            metadatas=[{"version": version, "chunk": i} for i in range(len(chunks))],
        )
    return len(chunks)


def embed_career_facts(user_id: str, facts: list[str], version: int) -> None:
    """Replace resume-derived facts in the memory namespace.

    Deletes all previously stored resume facts first so re-uploads don't
    accumulate stale facts from old versions. Session-derived facts
    (source='session') are left untouched.
    """
    if not facts:
        return
    mem_ns = chroma.memory_ns(user_id)
    chroma.delete_where(mem_ns, {"source": "resume"})
    chroma.upsert(
        mem_ns,
        documents=facts,
        metadatas=[{"source": "resume", "version": version} for _ in facts],
    )
