"""RAG tool — query the user's resume + document namespaces in ChromaDB."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from db import chroma

DESCRIPTION = (
    "Retrieve relevant snippets from the user's master resume and uploaded "
    "career documents to ground answers about their background."
)


class ToolSchema(BaseModel):
    query: str = Field(..., description="Natural-language search query.")
    n_results: int = Field(5, ge=1, le=20)
    include_docs: bool = Field(True, description="Also search uploaded documents.")


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)
    results: list[dict[str, Any]] = []

    for namespace, source in _namespaces(user_id, args.include_docs):
        docs, metas, dists = chroma.query(namespace, args.query, args.n_results)
        for doc, meta, dist in zip(docs, metas, dists):
            results.append(
                {
                    "source": source,
                    "text": doc,
                    "metadata": meta,
                    "distance": dist,
                }
            )

    # Most similar first (smaller cosine distance = more similar).
    results.sort(key=lambda r: r["distance"])
    results = results[: args.n_results]
    return {
        "tool": "rag",
        "query": args.query,
        "results": results,
        "context": "\n\n".join(r["text"] for r in results),
    }


def _namespaces(user_id: str, include_docs: bool):
    yield chroma.resume_ns(user_id), "resume"
    if include_docs:
        yield chroma.docs_ns(user_id), "document"
