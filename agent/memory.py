"""Two-tier memory: episodic (SQLite) + semantic career facts (ChromaDB).

- ``load_memories``  : session-start retrieval, merged into the system prompt.
- ``save_memories``  : session-end write (GPT-4o summary + extracted facts).
"""

from __future__ import annotations

import json
from typing import Any

from agent.llm import complete_json
from db import chroma, sqlite

_SUMMARY_SYSTEM = """You summarise a job-search chat session for long-term memory. \
Return a JSON object:
{
  "summary": "2-4 sentence recap of what the user worked on and decided",
  "facts": ["durable career fact or preference learned this session", ...]
}
Facts must be self-contained and worth remembering across sessions (skills, \
target companies/roles, constraints, preferences). Return [] if none."""


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

    # De-duplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for item in merged:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def save_memories(
    user_id: str, session_id: str, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarise the session and persist episodic summary + semantic facts."""
    if not messages:
        return {"summary": "", "facts": []}

    transcript = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
    )
    result = await complete_json(_SUMMARY_SYSTEM, transcript[:16000])
    summary = result.get("summary", "") if isinstance(result, dict) else ""
    facts = result.get("facts", []) if isinstance(result, dict) else []

    if summary:
        sqlite.add_episodic_memory(user_id, session_id, summary)
    if facts:
        chroma.upsert(
            chroma.memory_ns(user_id),
            documents=facts,
            metadatas=[
                {"source": "session", "session_id": session_id} for _ in facts
            ],
        )
    return {"summary": summary, "facts": facts}


def build_memory_prompt(memories: list[str]) -> str:
    if not memories:
        return ""
    body = "\n".join(f"- {m}" for m in memories)
    return (
        "Here is what you remember about this user from prior sessions and "
        f"extracted career facts:\n{body}\n"
    )
