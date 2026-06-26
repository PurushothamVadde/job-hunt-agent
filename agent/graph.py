"""LangGraph orchestration for JobHuntAI.

Node flow:

    plan -> tool_select -> tool_execute -> (loop to tool_select | synthesize)
         -> respond -> auto_summarize -> store_episodic / extract_facts / flush_trace

``tool_execute`` calls ``interrupt()`` when the selected tool is HITL-gated. The
graph uses a ``MemorySaver`` checkpointer so execution can be resumed across
HTTP requests via ``POST /chat/approve`` (see ``api/chat.py``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agent.llm import complete_json, complete_text
from agent.memory import build_memory_prompt
from agent.tools import HITL_TOOLS, TOOLS, tool_descriptions
from observability import langsmith


class AgentState(TypedDict, total=False):
    session_id: str
    user_id: str
    messages: list[BaseMessage]
    memories: list[str]
    tool_results: list[dict]
    pending_goals: list[str]
    hitl_pending: bool
    hitl_decision: Optional[str]  # "approve" | "edit" | "reject"
    hitl_correction: Optional[str]
    # Internal scratch:
    current_tool: Optional[str]
    current_args: Optional[dict]
    final_response: Optional[str]
    run_id: Optional[str]


SYSTEM_PROMPT = (
    "You are JobHuntAI, an expert AI job-search assistant. You help users find "
    "roles, research companies, tailor ATS-safe resumes, and apply. Be concise, "
    "proactive, and honest. Never fabricate resume content."
)

_PLANNER_SYSTEM = (
    "You are the planning module of a job-search agent. Given the conversation "
    "and the user's latest message, decide whether tools are needed. Available "
    "tools:\n{tools}\n\n"
    "Return JSON: {{\"goals\": [\"short sub-goal\", ...]}}. Return an empty list "
    "if the message can be answered directly without tools."
)

_SELECTOR_SYSTEM = (
    "You select the next tool to satisfy the current goal. Available tools:\n"
    "{tools}\n\n"
    "Tool input schemas are flexible dicts. Return JSON: "
    "{{\"tool\": \"<tool name or null>\", \"args\": {{...}}, "
    "\"reason\": \"...\"}}. Return tool=null when no remaining goal needs a tool."
)


@lru_cache(maxsize=1)
def _checkpointer() -> MemorySaver:
    return MemorySaver()


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
async def plan_node(state: AgentState) -> dict[str, Any]:
    state.setdefault("tool_results", [])
    last_user = _last_user_text(state["messages"])
    prompt = _PLANNER_SYSTEM.format(tools=tool_descriptions())
    result = await complete_json(prompt, _transcript(state) + f"\n\nLatest: {last_user}")
    goals = result.get("goals", []) if isinstance(result, dict) else []
    return {"pending_goals": goals}


async def tool_select_node(state: AgentState) -> dict[str, Any]:
    goals = state.get("pending_goals", [])
    if not goals:
        return {"current_tool": None, "current_args": None}

    goal = goals[0]
    prompt = _SELECTOR_SYSTEM.format(tools=tool_descriptions())
    context = (
        f"Current goal: {goal}\n\n"
        f"Conversation:\n{_transcript(state)}\n\n"
        f"Results so far: {json.dumps(state.get('tool_results', []))[:4000]}"
    )
    decision = await complete_json(prompt, context)
    tool = decision.get("tool") if isinstance(decision, dict) else None
    args = decision.get("args", {}) if isinstance(decision, dict) else {}
    if tool not in TOOLS:
        tool = None
    return {"current_tool": tool, "current_args": args}


async def tool_execute_node(state: AgentState) -> dict[str, Any]:
    tool = state.get("current_tool")
    goals = list(state.get("pending_goals", []))
    results = list(state.get("tool_results", []))

    if not tool:
        # Nothing to execute; drop the head goal to make progress.
        if goals:
            goals.pop(0)
        return {"pending_goals": goals, "current_tool": None}

    args = dict(state.get("current_args") or {})

    # ---- HITL gate ----------------------------------------------------- #
    if tool in HITL_TOOLS:
        action = HITL_TOOLS[tool]
        decision = interrupt(
            {
                "type": "hitl_request",
                "action": action,
                "details": {"tool": tool, "args": args},
            }
        )
        # On resume, ``decision`` is the payload injected via Command(resume=...).
        if isinstance(decision, dict):
            verdict = decision.get("decision", "approve")
            correction = decision.get("correction")
        else:
            verdict = str(decision) if decision else "approve"
            correction = None

        if verdict == "reject":
            if goals:
                goals.pop(0)
            results.append(
                {"tool": tool, "status": "rejected", "action": action}
            )
            return {
                "pending_goals": goals,
                "tool_results": results,
                "hitl_pending": False,
                "hitl_decision": verdict,
                "current_tool": None,
            }
        if verdict == "edit" and correction:
            args.update(_merge_correction(correction))
        # Approve / edit -> mark args approved so the tool commits its write.
        args["approved"] = True

    # ---- Execute ------------------------------------------------------- #
    try:
        module = TOOLS[tool]
        output = await module.run(args, state["user_id"])
    except Exception as exc:
        output = {"tool": tool, "error": str(exc)}

    results.append(output)
    if goals:
        goals.pop(0)

    return {
        "tool_results": results,
        "pending_goals": goals,
        "hitl_pending": False,
        "current_tool": None,
        "current_args": None,
    }


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    memory_prompt = build_memory_prompt(state.get("memories", []))
    tool_context = json.dumps(state.get("tool_results", []))[:8000]
    system = SYSTEM_PROMPT + ("\n\n" + memory_prompt if memory_prompt else "")
    user = (
        f"Conversation:\n{_transcript(state)}\n\n"
        f"Tool results (JSON):\n{tool_context}\n\n"
        "Write the assistant's final reply to the user, using the tool results "
        "where relevant."
    )
    response = await complete_text(system, user, temperature=0.4)
    return {"final_response": response}


async def respond_node(state: AgentState) -> dict[str, Any]:
    text = state.get("final_response") or ""
    messages = list(state["messages"])
    messages.append(AIMessage(content=text))
    return {"messages": messages}


async def auto_summarize_node(state: AgentState) -> dict[str, Any]:
    # Post-turn bookkeeping. Episodic/semantic writes happen at session end via
    # agent.memory.save_memories; here we just flush observability.
    langsmith.flush_trace()
    return {}


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def route_after_select(state: AgentState) -> str:
    return "tool_execute"


def route_after_execute(state: AgentState) -> str:
    """Loop back to select while goals remain, else synthesize."""
    if state.get("pending_goals"):
        return "tool_select"
    return "synthesize"


def route_after_plan(state: AgentState) -> str:
    return "tool_select" if state.get("pending_goals") else "synthesize"


# --------------------------------------------------------------------------- #
# Graph assembly
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("tool_select", tool_select_node)
    g.add_node("tool_execute", tool_execute_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("respond", respond_node)
    g.add_node("auto_summarize", auto_summarize_node)

    g.set_entry_point("plan")
    g.add_conditional_edges(
        "plan", route_after_plan, {"tool_select": "tool_select", "synthesize": "synthesize"}
    )
    g.add_conditional_edges(
        "tool_select", route_after_select, {"tool_execute": "tool_execute"}
    )
    g.add_conditional_edges(
        "tool_execute",
        route_after_execute,
        {"tool_select": "tool_select", "synthesize": "synthesize"},
    )
    g.add_edge("synthesize", "respond")
    g.add_edge("respond", "auto_summarize")
    g.add_edge("auto_summarize", END)

    return g.compile(checkpointer=_checkpointer())


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def initial_state(
    user_id: str, session_id: str, user_message: str, memories: list[str]
) -> AgentState:
    return {
        "session_id": session_id,
        "user_id": user_id,
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ],
        "memories": memories,
        "tool_results": [],
        "pending_goals": [],
        "hitl_pending": False,
        "hitl_decision": None,
        "hitl_correction": None,
    }


def _transcript(state: AgentState) -> str:
    lines = []
    for m in state.get("messages", []):
        role = getattr(m, "type", "user")
        lines.append(f"{role}: {getattr(m, 'content', '')}")
    return "\n".join(lines)[:8000]


def _last_user_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def _merge_correction(correction: str) -> dict[str, Any]:
    """Accept a JSON object string or treat it as free-text instruction."""
    try:
        parsed = json.loads(correction)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {"correction": correction}
