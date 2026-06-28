"""Format retrieved memories into a system-prompt block."""

from __future__ import annotations


def build_memory_prompt(memories: list[str]) -> str:
    if not memories:
        return ""
    body = "\n".join(f"- {m}" for m in memories)
    return (
        "Here is what you remember about this user from prior sessions and "
        f"extracted career facts:\n{body}\n"
    )
