"""Session-start memory retrieval — merges episodic summaries with semantic facts."""

from __future__ import annotations

from db import chroma, sqlite


async def load_memories(
    user_id: str, session_id: str, message: str, *, top_k: int = 5
) -> list[str]:
    """Merge the last N episodic summaries with top-K semantic facts."""
    merged: list[str] = []

    for ep in sqlite.get_recent_episodic(user_id, limit=5):
        merged.append(f"[past session] {ep['summary']}")

    docs, _metas, _dists = chroma.query(
        chroma.memory_ns(user_id), message or "career background", top_k
    )
    for fact in docs:
        merged.append(f"[fact] {fact}")

    seen: set[str] = set()
    out: list[str] = []
    for item in merged:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
