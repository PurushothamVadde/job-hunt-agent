"""Agent tools.

Each tool module exposes:
  * a ``ToolSchema`` Pydantic model validating the tool input
  * an async ``run(input: dict, user_id: str) -> dict`` coroutine

The ``TOOLS`` registry below maps tool name -> module, and ``HITL_TOOLS`` lists
the tool names that require a human-in-the-loop gate before execution.
"""

from __future__ import annotations

from . import (
    auto_apply,
    company_job_search,
    company_research,
    mcp_fs,
    rag,
    resume_tailor,
)

TOOLS = {
    "rag": rag,
    "company_job_search": company_job_search,
    "resume_tailor": resume_tailor,
    "company_research": company_research,
    "auto_apply": auto_apply,
    "mcp_fs": mcp_fs,
}

# Tools whose execution must pause for human approval (interrupt()).
HITL_TOOLS = {
    "resume_tailor": "write_resume",
    "auto_apply": "auto_apply",
}


def tool_descriptions() -> str:
    """Human-readable catalogue used in the planner prompt."""
    return "\n".join(
        f"- {name}: {getattr(mod, 'DESCRIPTION', 'No description.')}"
        for name, mod in TOOLS.items()
    )


__all__ = ["TOOLS", "HITL_TOOLS", "tool_descriptions"]
