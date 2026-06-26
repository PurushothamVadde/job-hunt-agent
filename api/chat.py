"""Chat endpoints: SSE streaming graph run + HITL approval resume.

``POST /chat/stream``  runs the LangGraph agent and streams SSE events.
``POST /chat/approve`` injects a HITL decision and resumes the suspended graph.

Graph state is checkpointed (see ``agent.graph``) and keyed by ``session_id`` via
LangGraph's ``thread_id`` config, so a suspended run can be resumed across HTTP
requests. We also track lightweight per-session metadata in a module-level dict.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agent.graph import build_graph, initial_state
from agent.memory import load_memories
from api.auth import get_current_user
from db import sqlite
from observability import langsmith

router = APIRouter(prefix="/chat", tags=["chat"])

# Per-session bookkeeping for in-flight HITL runs.
SESSION_STATE: dict[str, dict[str, Any]] = {}


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(None)
    message: str = Field(...)


class ApproveRequest(BaseModel):
    session_id: str
    decision: str = Field(..., description="approve | edit | reject")
    correction: Optional[str] = Field(None)


def _sse(event: dict[str, Any]) -> dict[str, str]:
    return {"data": json.dumps(event)}


def _graph_config(session_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": session_id}}


# --------------------------------------------------------------------------- #
# Streaming run
# --------------------------------------------------------------------------- #
@router.post("/stream")
async def chat_stream(
    req: ChatRequest, user: dict[str, Any] = Depends(get_current_user)
):
    user_id = user["user_id"]

    # Onboarding gate: require a resume profile before chatting.
    if not sqlite.get_current_resume(user_id):
        async def onboarding() -> AsyncIterator[dict[str, str]]:
            yield _sse(
                {
                    "type": "onboarding_required",
                    "message": "Please upload your master resume to get started.",
                }
            )
            yield _sse({"type": "done"})

        return EventSourceResponse(onboarding())

    # Ensure a session exists.
    session_id = req.session_id
    if not session_id or not sqlite.get_session(session_id):
        session = sqlite.create_session(user_id, title=req.message[:60])
        session_id = session["session_id"]
    sqlite.touch_session(session_id)
    sqlite.add_message(session_id, "user", req.message)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        memories = await load_memories(user_id, session_id, req.message)
        state = initial_state(user_id, session_id, req.message, memories)
        config = _graph_config(session_id)
        run_id = langsmith.create_run(
            "chat_turn",
            {"message": req.message},
            metadata={"user_id": user_id, "session_id": session_id},
        )

        try:
            async for event in _run_graph(state, config):
                yield event
        except Exception as exc:  # pragma: no cover - defensive
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            langsmith.end_run(run_id)
            langsmith.flush_trace()

    return EventSourceResponse(event_stream())


# --------------------------------------------------------------------------- #
# HITL approval -> resume
# --------------------------------------------------------------------------- #
@router.post("/approve")
async def chat_approve(
    req: ApproveRequest, user: dict[str, Any] = Depends(get_current_user)
):
    config = _graph_config(req.session_id)
    resume_payload = {"decision": req.decision, "correction": req.correction}

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in _run_graph(
                Command(resume=resume_payload), config
            ):
                yield event
        except Exception as exc:  # pragma: no cover
            yield _sse({"type": "error", "message": str(exc)})

    return EventSourceResponse(event_stream())


# --------------------------------------------------------------------------- #
# Graph driver -> SSE events
# --------------------------------------------------------------------------- #
async def _run_graph(
    inp: Any, config: dict[str, Any]
) -> AsyncIterator[dict[str, str]]:
    """Drive ``graph.astream`` and translate node updates into SSE events."""
    graph = build_graph()
    session_id = config["configurable"]["thread_id"]

    async for chunk in graph.astream(inp, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            # Interrupt surfaces under the reserved ``__interrupt__`` key.
            if node == "__interrupt__":
                payload = _interrupt_payload(update)
                if payload:
                    yield _sse(payload)
                continue

            async for ev in _events_for_node(node, update, session_id):
                yield ev

    # If the graph finished (not interrupted), stream the final response + done.
    snapshot = graph.get_state(config)
    if not snapshot.next:
        values = snapshot.values
        text = values.get("final_response") or ""
        if text:
            sqlite.add_message(session_id, "assistant", text)
            async for tok in _emit_tokens(text):
                yield tok
        yield _sse({"type": "done"})


async def _events_for_node(
    node: str, update: dict[str, Any], session_id: str
) -> AsyncIterator[dict[str, str]]:
    if update is None:
        return
    if node == "plan":
        goals = update.get("pending_goals", [])
        if goals:
            yield _sse({"type": "progress", "step": f"Planning: {', '.join(goals)}"})
    elif node == "tool_select":
        tool = update.get("current_tool")
        if tool:
            yield _sse({"type": "tool_start", "tool": tool})
    elif node == "tool_execute":
        for result in update.get("tool_results", [])[-1:]:
            tool = result.get("tool", "tool")
            yield _sse({"type": "tool_end", "tool": tool, "result": result})
            async for extra in _result_side_events(result):
                yield extra


async def _result_side_events(result: dict[str, Any]) -> AsyncIterator[dict[str, str]]:
    """Emit specialised SSE events derived from a tool result."""
    if result.get("type") == "captcha_blocked":
        yield _sse({"type": "captcha_blocked"})
    if result.get("type") == "login_required":
        yield _sse({"type": "login_required", "url": result.get("url", "")})
    if result.get("resume_ready") and result.get("path"):
        yield _sse({"type": "resume_ready", "path": result["path"]})
    if result.get("applied"):
        yield _sse(
            {
                "type": "applied",
                "company": result.get("company", ""),
                "role": result.get("role", ""),
            }
        )


async def _emit_tokens(text: str) -> AsyncIterator[dict[str, str]]:
    for token in text.split(" "):
        yield _sse({"type": "token", "content": token + " "})
        await asyncio.sleep(0)


def _interrupt_payload(update: Any) -> Optional[dict[str, Any]]:
    """Extract the hitl_request payload from an interrupt update."""
    items = update if isinstance(update, (list, tuple)) else [update]
    for item in items:
        val = getattr(item, "value", item)
        if isinstance(val, dict) and val.get("type") == "hitl_request":
            return val
    return None
