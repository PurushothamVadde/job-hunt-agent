"""Session-end memory persistence — GPT-4o summary + semantic fact extraction."""

from __future__ import annotations

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
