"""AgentState definition and initial_state factory."""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

SYSTEM_PROMPT = (
    "You are JobHuntAI, an expert AI job-search assistant. You help users find "
    "roles, research companies, tailor ATS-safe resumes, and apply. Be concise, "
    "proactive, and honest. Never fabricate resume content.\n\n"
    "Job search rules:\n"
    "- Always search one company at a time.\n"
    "- Before calling company_job_search you need: (1) company name, "
    "(2) country, and (3) city/state/region.\n"
    "- INFER country from well-known cities — Dallas/Houston/Chicago → USA, "
    "London/Manchester → UK, Toronto/Vancouver → Canada, Sydney/Melbourne → Australia. "
    "Never ask for country when the city makes it obvious.\n"
    "- Only ask for missing info that you genuinely cannot infer from context or "
    "the conversation history.\n"
    "- Remote jobs are always included by default alongside location-specific results — "
    "tell the user this so they know.\n"
    "- After presenting results for one company, ask whether to search another "
    "company or proceed with the found roles.\n\n"
    "Resume tailoring rules:\n"
    "- When the user picks a job from search results, you already have its URL — "
    "pass it as ``job_url`` to resume_tailor. Do NOT ask the user to paste the "
    "job description; fetch it automatically.\n"
    "- Only ask the user for jd_text if no URL is available."
)


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
    current_tool: Optional[str]
    current_args: Optional[dict]
    final_response: Optional[str]
    run_id: Optional[str]
    # Persists across turns — job listings from the most recent search so the
    # selector can reliably extract URLs without re-running the search tool.
    last_search_results: list[dict]
    # Persists across turns — metadata about the most recently tailored resume
    # so the agent knows not to re-tailor and can proceed to auto_apply.
    last_tailored_resume: Optional[dict]
    # Tracks which auto_apply phase completed last turn: "plan" | "fill" | None
    # so the selector always advances to the next phase instead of restarting.
    last_apply_phase: Optional[str]


def initial_state(
    user_id: str,
    session_id: str,
    user_message: str,
    memories: list[str],
    history: Optional[list[dict]] = None,
    last_search_results: Optional[list[dict]] = None,
    last_tailored_resume: Optional[dict] = None,
    last_apply_phase: Optional[str] = None,
) -> AgentState:
    from langchain_core.messages import AIMessage

    history_messages: list[BaseMessage] = []
    for turn in (history or []):
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            history_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            history_messages.append(AIMessage(content=content))

    return {
        "session_id": session_id,
        "user_id": user_id,
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            *history_messages,
            HumanMessage(content=user_message),
        ],
        "memories": memories,
        "tool_results": [],
        "pending_goals": [],
        "hitl_pending": False,
        "hitl_decision": None,
        "hitl_correction": None,
        "last_search_results": last_search_results or [],
        "last_tailored_resume": last_tailored_resume,
        "last_apply_phase": last_apply_phase,
    }
