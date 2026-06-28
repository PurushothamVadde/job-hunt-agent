"""MCP-style filesystem tool.

Provides sandboxed read/write/list access to the user's resume workspace so the
agent can inspect generated PDFs, tailored profiles, and uploaded artifacts.
All paths are confined to ``RESUMES_DIR`` to prevent traversal.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

DESCRIPTION = (
    "Sandboxed filesystem access (list / read / write) within the user's "
    "resume workspace directory."
)

ROOT = Path(os.getenv("RESUMES_DIR", "resumes")).resolve()


class ToolSchema(BaseModel):
    action: str = Field(..., description="list | read | write")
    path: str = Field("", description="Relative path within the workspace.")
    content: Optional[str] = Field(None, description="Content for write actions.")


def _safe(rel: str) -> Path:
    target = (ROOT / rel).resolve()
    if ROOT not in target.parents and target != ROOT:
        raise ValueError("Path escapes the sandbox root.")
    return target


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)
    ROOT.mkdir(parents=True, exist_ok=True)

    try:
        target = _safe(args.path)
    except ValueError as exc:
        return {"tool": "mcp_fs", "error": str(exc)}

    if args.action == "list":
        base = target if target.is_dir() else ROOT
        entries = [
            {"name": p.name, "is_dir": p.is_dir(), "size": p.stat().st_size}
            for p in sorted(base.glob("*"))
        ]
        return {"tool": "mcp_fs", "action": "list", "entries": entries}

    if args.action == "read":
        if not target.exists() or target.is_dir():
            return {"tool": "mcp_fs", "error": "file not found"}
        is_binary = target.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg")
        return {
            "tool": "mcp_fs",
            "action": "read",
            "path": str(target),
            "binary": is_binary,
            "content": None if is_binary else target.read_text(encoding="utf-8", errors="ignore"),
            "size": target.stat().st_size,
        }

    if args.action == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.content or "", encoding="utf-8")
        return {"tool": "mcp_fs", "action": "write", "path": str(target)}

    return {"tool": "mcp_fs", "error": f"unknown action '{args.action}'"}
