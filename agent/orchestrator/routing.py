"""Conditional edge routing functions for the LangGraph state machine."""

from __future__ import annotations

from agent.orchestrator.state import AgentState


def route_after_plan(state: AgentState) -> str:
    return "tool_select" if state.get("pending_goals") else "synthesize"


def route_after_select(state: AgentState) -> str:
    return "tool_execute" if state.get("current_tool") else "synthesize"


def route_after_execute(state: AgentState) -> str:
    """Loop back to select while goals remain, else synthesize."""
    return "tool_select" if state.get("pending_goals") else "synthesize"
