"""ChromaDB wrapper for JobHuntAI.

Each *namespace* maps to a dedicated Chroma collection. Namespaces follow the
convention:

    memory:{user_id}   -> career facts (semantic memory)
    resume:{user_id}   -> chunked master resume (512 tok / 50 overlap)
    docs:{user_id}     -> uploaded career documents

Embeddings are produced locally with ``sentence-transformers/all-MiniLM-L6-v2``
through Chroma's ``SentenceTransformerEmbeddingFunction`` so no network calls are
required for vectorisation.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", ".chroma")
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Chroma collection names may only contain [a-zA-Z0-9._-]; ':' is not allowed.
# We therefore translate the logical namespace separator on the way in.
_SEP = "__"


def _normalize(namespace: str) -> str:
    return namespace.replace(":", _SEP)


@lru_cache(maxsize=1)
def _client() -> chromadb.ClientAPI:
    Path(CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)


@lru_cache(maxsize=1)
def _embedding_fn():
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )


def init_chroma() -> None:
    """Eagerly initialise the ChromaDB client and embedding model on startup."""
    _client()
    _embedding_fn()


def _collection(namespace: str):
    return _client().get_or_create_collection(
        name=_normalize(namespace),
        embedding_function=_embedding_fn(),
        metadata={"hnsw:space": "cosine"},
    )


# Convenience namespace builders ------------------------------------------- #
def memory_ns(user_id: str) -> str:
    return f"memory:{user_id}"


def resume_ns(user_id: str) -> str:
    return f"resume:{user_id}"


def docs_ns(user_id: str) -> str:
    return f"docs:{user_id}"


# CRUD --------------------------------------------------------------------- #
def upsert(
    namespace: str,
    documents: list[str],
    metadatas: Optional[list[dict[str, Any]]] = None,
    ids: Optional[list[str]] = None,
) -> list[str]:
    """Insert or update ``documents`` in ``namespace``.

    Returns the list of ids written.
    """
    if not documents:
        return []
    if ids is None:
        ids = [str(uuid.uuid4()) for _ in documents]
    if metadatas is None:
        metadatas = [{} for _ in documents]
    # Chroma rejects empty metadata dicts in some versions; ensure non-empty.
    metadatas = [m if m else {"_": ""} for m in metadatas]
    _collection(namespace).upsert(documents=documents, metadatas=metadatas, ids=ids)
    return ids


def query(
    namespace: str,
    query_text: str,
    n_results: int = 5,
) -> tuple[list[str], list[dict[str, Any]], list[float]]:
    """Semantic search. Returns ``(documents, metadatas, distances)``."""
    col = _collection(namespace)
    count = col.count()
    if count == 0:
        return [], [], []
    res = col.query(query_texts=[query_text], n_results=min(n_results, count))
    documents = res.get("documents", [[]])[0]
    metadatas = res.get("metadatas", [[]])[0]
    distances = res.get("distances", [[]])[0]
    return documents, metadatas, distances


def delete_namespace(namespace: str) -> None:
    """Drop an entire collection (e.g. when re-ingesting a resume)."""
    try:
        _client().delete_collection(name=_normalize(namespace))
    except Exception:
        # Collection may not exist yet — that's fine.
        pass


def delete_where(namespace: str, where: dict[str, Any]) -> int:
    """Delete all documents matching a metadata filter. Returns count deleted.

    Example: delete_where(memory_ns(user_id), {"source": "resume"})
    """
    col = _collection(namespace)
    if col.count() == 0:
        return 0
    result = col.get(where=where)
    ids = result.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


def count(namespace: str) -> int:
    return _collection(namespace).count()


if __name__ == "__main__":
    ns = memory_ns("test_user")
    delete_namespace(ns)
    upsert(
        ns,
        documents=[
            "Senior backend engineer with 8 years of Python experience.",
            "Led migration of monolith to microservices at Acme Corp.",
            "Prefers remote-first companies in fintech.",
        ],
        metadatas=[{"kind": "skill"}, {"kind": "achievement"}, {"kind": "preference"}],
    )
    docs, metas, dists = query(ns, "What is the candidate's preferred company?", 2)
    for d, m, dist in zip(docs, metas, dists):
        print(f"[{dist:.3f}] {m} :: {d}")
