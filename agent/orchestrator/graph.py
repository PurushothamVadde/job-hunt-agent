"""Graph assembly — wires nodes and edges into a compiled LangGraph."""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agent.orchestrator.nodes import (
    auto_summarize_node,
    plan_node,
    respond_node,
    synthesize_node,
    tool_execute_node,
    tool_select_node,
)
from agent.orchestrator.routing import (
    route_after_execute,
    route_after_plan,
    route_after_select,
)
from agent.orchestrator.state import AgentState


@lru_cache(maxsize=1)
def _checkpointer() -> MemorySaver:
    return MemorySaver()


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
        "tool_select", route_after_select, {"tool_execute": "tool_execute", "synthesize": "synthesize"}
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
