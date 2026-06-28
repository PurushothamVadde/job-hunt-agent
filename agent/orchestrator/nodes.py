"""LangGraph node implementations."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import interrupt

from agent.llm import complete_json, complete_text
from agent.memory import build_memory_prompt
from agent.orchestrator.state import SYSTEM_PROMPT, AgentState
from agent.tools import HITL_TOOLS, TOOLS, tool_descriptions
from observability import langsmith

_PLANNER_SYSTEM = (
    "You are the planning module of a job-search agent. Given the conversation "
    "and the user's latest message, decide whether tools are needed. Available "
    "tools:\n{tools}\n\n"
    "Job-search rules:\n"
    "- Only search one company at a time.\n"
    "- ALWAYS infer country from well-known cities (Dallas/Houston/Chicago/NYC → USA, "
    "London/Manchester → UK, Toronto/Vancouver → Canada, Sydney/Melbourne → Australia). "
    "Look through the full conversation history — the user may have already given city/country.\n"
    "- CRITICAL: Do NOT call company_job_search if job listings are already visible in "
    "the conversation. If the user refers to a job that was just shown, that job already "
    "exists — do not search again.\n\n"
    "Resume tailoring and apply rules:\n"
    "- When the user says 'apply for', 'tailor my resume for', or picks a specific job "
    "that was already shown in the conversation, the goal is ONLY resume_tailor. "
    "Do NOT include company_job_search as a goal in this case.\n"
    "- The job URL is already in the conversation — the selector will extract it.\n"
    "- If the user says 'I have logged in' / 'I created an account' / 'continue applying' "
    "and a resume is already tailored, the goal is auto_apply (phase=fill). "
    "Do NOT re-tailor or re-search.\n"
    "- If the user provides Workday credentials (workday_email / workday_password) in JSON "
    "format, the goal is auto_apply (phase=fill) with those credentials passed.\n"
    "- CRITICAL: if last_apply_phase='plan', the plan is DONE — the goal is "
    "auto_apply (phase=fill), NOT phase=plan again. Never repeat a phase.\n"
    "- If last_apply_phase='fill', the goal is auto_apply (phase=submit).\n\n"
    "Return JSON: {{\"goals\": [\"short sub-goal\", ...]}}. Return an empty list "
    "if the message can be answered directly without tools."
)

_SELECTOR_SYSTEM = (
    "You select the next tool to satisfy the current goal. Available tools:\n"
    "{tools}\n\n"
    "Tool input schemas are flexible dicts. Return JSON: "
    "{{\"tool\": \"<tool name or null>\", \"args\": {{...}}, "
    "\"reason\": \"...\"}}. Return tool=null when no remaining goal needs a tool.\n\n"
    "IMPORTANT — resume_tailor args:\n"
    "- When the user picks a job already shown in the conversation, look through the "
    "conversation history and tool results to find that job's URL. Pass it as "
    "``job_url`` (not jd_text). Do NOT call company_job_search to re-fetch jobs.\n"
    "- Set ``company`` and ``role`` from the job title/company name in conversation.\n"
    "- Never leave both jd_text and job_url empty.\n\n"
    "IMPORTANT — auto_apply args:\n"
    "- url: Use last_tailored_resume.url (the job URL stored when tailoring). "
    "  If that field is null/missing, find the matching job URL in last_search_results "
    "  by matching the tailored company and role.\n"
    "- company: last_tailored_resume.company\n"
    "- role: last_tailored_resume.role\n"
    "- resume_path: last_tailored_resume.path\n"
    "- phase: always start with 'plan'\n"
    "- NEVER call auto_apply with an empty url, company, or role. If you cannot find "
    "  the URL from last_tailored_resume or last_search_results, return tool=null and "
    "  explain what information is missing.\n"
    "- If the user's latest message contains a JSON object with workday_email and/or "
    "  workday_password, include those fields in auto_apply args.\n"
    "- When phase='fill': also set headless=false so the browser window is visible to "
    "  the user (helps with Workday login)."
)


async def plan_node(state: AgentState) -> dict[str, Any]:
    state.setdefault("tool_results", [])
    last_user = _last_user_text(state["messages"])
    prompt = _PLANNER_SYSTEM.format(tools=tool_descriptions())

    # Give the planner structural context it cannot infer from the transcript alone
    extra: list[str] = []
    if state.get("last_search_results"):
        titles = [j.get("title", "") for j in state["last_search_results"][:5]]
        extra.append(f"Jobs already found (do NOT search again): {', '.join(titles)}")
    if state.get("last_tailored_resume"):
        tr = state["last_tailored_resume"]
        extra.append(
            f"Resume already tailored for {tr.get('role')} at {tr.get('company')} "
            f"(do NOT call resume_tailor again). Next step: auto_apply if user wants to apply."
        )
    apply_phase = state.get("last_apply_phase")
    if apply_phase == "plan":
        extra.append(
            "auto_apply phase=plan is DONE. Next goal must be auto_apply phase=fill. "
            "Do NOT call phase=plan again."
        )
    elif apply_phase == "fill":
        extra.append(
            "auto_apply phase=fill is DONE (form filled + screenshot taken). "
            "Next goal must be auto_apply phase=submit if user wants to submit."
        )
    extra_ctx = ("\n\nCurrent state:\n" + "\n".join(extra)) if extra else ""

    result = await complete_json(
        prompt,
        _transcript(state) + extra_ctx + f"\n\nLatest: {last_user}",
    )
    goals = result.get("goals", []) if isinstance(result, dict) else []
    return {"pending_goals": goals}


async def tool_select_node(state: AgentState) -> dict[str, Any]:
    goals = state.get("pending_goals", [])
    if not goals:
        return {"current_tool": None, "current_args": None}

    goal = goals[0]
    prompt = _SELECTOR_SYSTEM.format(tools=tool_descriptions())

    # Inject the most recent job search results so the selector can reliably
    # extract job URLs without re-running company_job_search.
    last_results = state.get("last_search_results", [])
    last_results_str = (
        json.dumps(
            [{"title": j.get("title"), "url": j.get("url"), "company": j.get("company"),
              "location": j.get("location")} for j in last_results[:10]],
            indent=2,
        )
        if last_results else "none"
    )

    tailored = state.get("last_tailored_resume")
    tailored_str = json.dumps(tailored) if tailored else "none"

    apply_phase = state.get("last_apply_phase")
    phase_guidance = ""
    if apply_phase == "plan":
        phase_guidance = (
            "\nCRITICAL: last_apply_phase=plan means the plan is DONE. "
            "You MUST use phase='fill' — never phase='plan'. "
            "Pass headless=false so the browser is visible. "
            "If the user's message contains workday_email and workday_password, "
            "include them in the args."
        )
    elif apply_phase == "fill":
        phase_guidance = (
            "\nCRITICAL: last_apply_phase=fill means the form is filled. "
            "You MUST use phase='submit'."
        )

    context = (
        f"Current goal: {goal}\n\n"
        f"Most recent job search results — use these URLs when calling resume_tailor:\n"
        f"{last_results_str}\n\n"
        f"Most recently tailored resume (already done — do NOT call resume_tailor again):\n"
        f"{tailored_str}\n\n"
        f"last_apply_phase: {apply_phase or 'none'}{phase_guidance}\n\n"
        f"Conversation:\n{_transcript(state)}\n\n"
        f"Results so far this turn: {json.dumps(state.get('tool_results', []))[:3000]}"
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
        if goals:
            goals.pop(0)
        return {"pending_goals": goals, "current_tool": None}

    args = dict(state.get("current_args") or {})

    # auto_apply plan phase is non-destructive — skip HITL, run immediately
    _skip_hitl = (tool == "auto_apply" and args.get("phase") == "plan")

    if tool in HITL_TOOLS and not _skip_hitl:
        action = HITL_TOOLS[tool]
        decision = interrupt(
            {
                "type": "hitl_request",
                "action": action,
                "details": {"tool": tool, "args": args},
            }
        )
        if isinstance(decision, dict):
            verdict = decision.get("decision", "approve")
            correction = decision.get("correction")
        else:
            verdict = str(decision) if decision else "approve"
            correction = None

        if verdict == "reject":
            if goals:
                goals.pop(0)
            results.append({"tool": tool, "status": "rejected", "action": action})
            return {
                "pending_goals": goals,
                "tool_results": results,
                "hitl_pending": False,
                "hitl_decision": verdict,
                "current_tool": None,
            }
        if verdict == "edit" and correction:
            args.update(_merge_correction(correction))
        args["approved"] = True

    try:
        module = TOOLS[tool]
        output = await module.run(args, state["user_id"])
    except Exception as exc:
        output = {"tool": tool, "error": str(exc)}

    results.append(output)
    if goals:
        goals.pop(0)

    update: dict[str, Any] = {
        "tool_results": results,
        "pending_goals": goals,
        "hitl_pending": False,
        "current_tool": None,
        "current_args": None,
    }

    # Persist job listings so the selector can access them in the next turn
    # without re-running the search tool.
    if tool == "company_job_search" and isinstance(output, dict):
        fresh = output.get("results", [])
        if fresh:
            update["last_search_results"] = fresh

    # Persist tailored resume metadata so the agent knows not to re-tailor and
    # auto_apply can read the job URL, path, company, and role without re-running.
    if tool == "resume_tailor" and isinstance(output, dict) and output.get("resume_ready"):
        update["last_tailored_resume"] = {
            "path":    output.get("path"),
            "company": output.get("company"),
            "role":    output.get("role"),
            "url":     output.get("job_url"),  # used by auto_apply
        }

    # Track which auto_apply phase just completed so the selector always
    # advances to the next phase rather than repeating.
    if tool == "auto_apply" and isinstance(output, dict) and "error" not in output:
        completed_phase = output.get("phase")  # "plan" | "fill" | "submit"
        if completed_phase:
            update["last_apply_phase"] = completed_phase
        # Reset after a successful submission
        if output.get("applied"):
            update["last_apply_phase"] = None

    return update


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    memory_prompt = build_memory_prompt(state.get("memories", []))
    tool_context = json.dumps(state.get("tool_results", []))[:8000]
    system = SYSTEM_PROMPT + ("\n\n" + memory_prompt if memory_prompt else "")
    user = (
        f"Conversation:\n{_transcript(state)}\n\n"
        f"Tool results (JSON):\n{tool_context}\n\n"
        "Write the assistant's final reply to the user based on the tool results above.\n"
        "Priority order for what to lead with:\n"
        "1. If resume_tailor succeeded (resume_ready=true): confirm it was tailored, "
        "show matched skills and missing skills. Do NOT include a download link — the PDF "
        "is sent as a file attachment automatically. Ask if they want to auto-apply.\n"
        "2. If resume_tailor ran but PDF is pending approval (resume_ready=false): summarise "
        "the gap analysis and ask the user to approve writing the PDF.\n"
        "3. If auto_apply returned type='login_required': explain that Workday requires an "
        "account. Tell the user they can either: (a) share their Workday credentials in the "
        "next message as JSON {\"workday_email\": \"...\", \"workday_password\": \"...\"} so "
        "the tool can sign in automatically, or (b) create an account manually at the URL "
        "shown, then say 'I've logged in' to re-try. DO NOT say the system will retry "
        "automatically.\n"
        "4. If auto_apply returned type='verify_email': tell the user to check their inbox "
        "and click the verification link, then come back.\n"
        "5. If auto_apply returned type='blocked' or type='incomplete': explain which step "
        "was blocked (use blocked_step if present), include validation_errors if available, "
        "and ask the user to fix the highlighted fields then retry.\n"
        "6. If auto_apply returned ready_to_submit=true: tell the user the form is filled "
        "and ask them to approve submission.\n"
        "7. If results contain job listings: present them clearly with title, location, fit score, URL.\n"
        "8. If auto_apply returned phase='plan': summarise the planned fields and ask the user "
        "to confirm they want to proceed with filling the form.\n"
        "9. If there was an error (look for 'error' key in tool results): show the exact error "
        "message from the 'error' field verbatim so the user knows what went wrong.\n"
        "Do NOT say you will search again — no further tool calls will be made this turn."
    )
    response = await complete_text(system, user, temperature=0.4)
    return {"final_response": response}


async def respond_node(state: AgentState) -> dict[str, Any]:
    text = state.get("final_response") or ""
    messages = list(state["messages"])
    messages.append(AIMessage(content=text))
    return {"messages": messages}


async def auto_summarize_node(state: AgentState) -> dict[str, Any]:
    langsmith.flush_trace()
    return {}


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #
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
