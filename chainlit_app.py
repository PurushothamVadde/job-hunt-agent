"""JobHuntAI — Chainlit frontend.

Provides:
  - OAuth login (Google + GitHub) via @cl.oauth_callback
  - Streaming chat backed by the LangGraph agent
  - Resume upload with live ingestion progress
  - HITL approval via cl.AskActionMessage
  - /dashboard command for applications + resume versions
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import json
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

import chainlit as cl
from langgraph.types import Command

from agent.memory import load_memories
from agent.orchestrator import build_graph, initial_state
from agent.resume.pipeline import ingest
from db import chroma, sqlite
from db.chainlit_data_layer import SQLiteDataLayer


# ── Dashboard data helper ─────────────────────────────────────────────────────
# Writes user dashboard JSON to public/ so the sidebar JS can fetch it
# via Chainlit's built-in /public/{filename:path} static file endpoint.

def _write_dashboard_cache(user_id: str) -> None:
    apps     = sqlite.list_applications(user_id) or []
    versions = sqlite.list_resume_versions(user_id) or []
    payload  = {"applications": apps, "resume_versions": versions}
    path     = os.path.join("public", f"dashboard_{user_id}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh)


@cl.data_layer
def get_data_layer() -> SQLiteDataLayer:
    return SQLiteDataLayer()


# ── Example prompts ───────────────────────────────────────────────────────────

_EXAMPLE_STARTERS = [
    cl.Starter(
        label="Find SWE jobs at Google in California",
        message="Find Software Engineer jobs at Google in California, USA",
    ),
    cl.Starter(
        label="PM roles at Meta in New York",
        message="Find Product Manager positions at Meta in New York, USA",
    ),
    cl.Starter(
        label="Data Scientist at Microsoft in Seattle",
        message="Search for Data Scientist roles at Microsoft in Seattle, USA",
    ),
    cl.Starter(
        label="Remote Backend roles at Stripe",
        message="Find remote Backend Engineer jobs at Stripe in San Francisco, USA",
    ),
]

_EXAMPLE_ACTIONS = [
    cl.Action(
        name="example_prompt",
        label="Find SWE jobs at Google in California",
        payload={"text": "Find Software Engineer jobs at Google in California, USA"},
    ),
    cl.Action(
        name="example_prompt",
        label="PM roles at Meta in New York",
        payload={"text": "Find Product Manager positions at Meta in New York, USA"},
    ),
    cl.Action(
        name="example_prompt",
        label="Data Scientist at Microsoft in Seattle",
        payload={"text": "Search for Data Scientist roles at Microsoft in Seattle, USA"},
    ),
    cl.Action(
        name="example_prompt",
        label="Remote Backend roles at Stripe",
        payload={"text": "Find remote Backend Engineer jobs at Stripe in San Francisco, USA"},
    ),
]


@cl.set_starters
async def set_starters() -> list[cl.Starter]:
    try:
        user: Optional[cl.User] = cl.user_session.get("user")
    except Exception:
        return []
    if not user:
        return []
    resume = sqlite.get_current_resume(user.identifier)
    return _EXAMPLE_STARTERS if resume else []


@cl.action_callback("example_prompt")
async def on_example_prompt(action: cl.Action) -> None:
    await action.remove()
    await on_message(cl.Message(content=action.payload.get("text", "")))


# ── Startup ───────────────────────────────────────────────────────────────────

@cl.on_app_startup
async def on_startup():
    sqlite.init_db()
    chroma.init_chroma()


# ── OAuth — Google / GitHub ───────────────────────────────────────────────────

@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, Any],
    default_user: cl.User,
) -> Optional[cl.User]:
    email = raw_user_data.get("email", "")
    name = raw_user_data.get("name") or raw_user_data.get("login", "")
    picture = raw_user_data.get("picture") or raw_user_data.get("avatar_url", "")
    if not email:
        return None
    user = sqlite.get_or_create_oauth_user(email, name, provider=provider_id, picture_url=picture)
    return cl.User(
        identifier=user["user_id"],
        metadata={"email": email, "name": name, "picture": picture},
    )


# ── Chat resume ───────────────────────────────────────────────────────────────

@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    user: Optional[cl.User] = cl.user_session.get("user")
    user_id = (user.identifier if user else None) or thread.get("userId", "")
    thread_id = thread.get("id") or cl.context.session.thread_id
    cl.user_session.set("user_id", user_id)
    cl.user_session.set("session_id", thread_id)
    if user_id:
        _write_dashboard_cache(user_id)


# ── Chat start ────────────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    user: Optional[cl.User] = cl.user_session.get("user")
    if not user:
        await cl.Message(content="Session expired. Please refresh and log in again.").send()
        return

    user_id = user.identifier
    cl.user_session.set("user_id", user_id)
    cl.user_session.set("session_id", cl.context.session.thread_id)

    name = (user.metadata or {}).get("name") or (user.metadata or {}).get("email", "")
    if not name:
        db_user = sqlite.get_user_by_id(user_id)
        name = (db_user or {}).get("display_name") or (db_user or {}).get("username", "")

    _write_dashboard_cache(user_id)

    resume = sqlite.get_current_resume(user_id)

    if not resume:
        await cl.Message(
            content=(
                f"Welcome to **JobHuntAI**, {name}!\n\n"
                "To get started, upload your resume (PDF or DOCX). "
                "I'll parse it, build your career profile, and help you tailor "
                "applications, research companies, and auto-apply."
            )
        ).send()
    else:
        await cl.Message(
            content=(
                f"Welcome back, **{name}**! How can I help today?\n\n"
                "- Ask me to find jobs, research companies, or tailor your resume\n"
                "- Type `/dashboard` to see your tracked applications\n"
                "- Upload a new file to refresh your resume profile"
            )
        ).send()


# ── Message handler ───────────────────────────────────────────────────────────

@cl.on_message
async def on_message(message: cl.Message):
    user: Optional[cl.User] = cl.user_session.get("user")
    user_id: str = (user.identifier if user else None) or cl.user_session.get("user_id", "")
    session_id: str = cl.user_session.get("session_id") or cl.context.session.thread_id

    if not user_id:
        await cl.Message(content="Session expired. Please refresh and log in again.").send()
        return

    # Lazy session creation on first message
    if not sqlite.get_session(session_id):
        title = message.content[:60].strip() or "New session"
        sqlite.create_session(user_id, session_id=session_id, title=title)

    # File upload → resume ingestion
    for el in message.elements or []:
        if el.mime in (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            await _handle_resume_upload(el, user_id)
            return

    # Dashboard command
    if message.content.strip().lower() in ("/dashboard", "show applications", "my applications"):
        await _show_dashboard(user_id)
        return

    # Pre-store Workday credentials if the user pasted them as JSON.
    # This ensures they are available to the tool even if the LLM selector
    # forgets to pass them explicitly in args.
    _maybe_store_workday_credentials(user_id, message.content)

    memories = await load_memories(user_id, session_id, message.content)
    history = sqlite.get_messages(session_id, limit=20)
    # The data layer's create_step already saved this message — exclude it from
    # the history slice so it isn't added again as a HumanMessage in state.
    history = [m for m in history if not (m["role"] == "user" and m["content"] == message.content)]
    last_search_results  = cl.user_session.get("last_search_results", [])
    last_tailored_resume = cl.user_session.get("last_tailored_resume")
    last_apply_phase     = cl.user_session.get("last_apply_phase")
    state = initial_state(
        user_id, session_id, message.content, memories,
        history=history,
        last_search_results=last_search_results,
        last_tailored_resume=last_tailored_resume,
        last_apply_phase=last_apply_phase,
    )
    config = {"configurable": {"thread_id": session_id}}
    await _run_agent(state, config)


# ── Agent streaming ───────────────────────────────────────────────────────────

async def _run_agent(inp: Any, config: dict) -> None:
    graph = build_graph()
    reply = cl.Message(content="Thinking...")
    await reply.send()
    got_response = False
    done = False
    status_label = "Thinking"
    loop = asyncio.get_running_loop()
    last_activity = loop.time()

    async def _heartbeat() -> None:
        tick = 0
        while not done:
            await asyncio.sleep(1.5)
            if done or got_response:
                break
            if (loop.time() - last_activity) < 2.5:
                continue
            dots = "." * ((tick % 3) + 1)
            reply.content = f"{status_label}{dots}"
            try:
                await reply.update()
            except Exception:
                return
            tick += 1

    heartbeat_task = asyncio.create_task(_heartbeat())
    stream = graph.astream(inp, config=config, stream_mode="updates")

    try:
        async for chunk in stream:
            for node, update in chunk.items():
                if update is None:
                    continue
                last_activity = loop.time()

                if node == "__interrupt__":
                    await _handle_hitl(update, config)
                    return

                if node == "plan":
                    status_label = "Planning"
                    goals = update.get("pending_goals", [])
                    if goals:
                        reply.content = f"Planning: {', '.join(goals)}..."
                        await reply.update()
                        async with cl.Step(name="Planning") as step:
                            step.output = ", ".join(goals)

                elif node == "tool_select":
                    tool = update.get("current_tool", "")
                    if tool:
                        status_label = f"Working on {tool}"
                        reply.content = f"Running **{tool}**..."
                        await reply.update()
                        async with cl.Step(name=f"Tool: {tool}"):
                            pass

                elif node == "tool_execute":
                    status_label = "Processing tool results"
                    for result in update.get("tool_results", [])[-1:]:
                        tool = result.get("tool", "tool")
                        async with cl.Step(name=f"{tool} result") as step:
                            step.output = str(result.get("result", ""))
                        # Send tailored PDF as a real file attachment
                        if (
                            tool == "resume_tailor"
                            and result.get("resume_ready")
                            and result.get("path")
                            and os.path.exists(result["path"])
                        ):
                            pdf_name = (
                                f"tailored_{result.get('company', 'resume')}_{result.get('role', '')}.pdf"
                                .replace(" ", "_").strip("_")
                            )
                            await cl.Message(
                                content="Your tailored resume is ready:",
                                elements=[cl.File(name=pdf_name, path=result["path"], display="inline")],
                            ).send()
                        # Show auto_apply screenshot for review
                        if (
                            tool == "auto_apply"
                            and result.get("screenshot")
                            and os.path.exists(result["screenshot"])
                        ):
                            shot_label = {
                                "login_required": "Workday login page",
                                "verify_email": "Account creation page",
                            }.get(result.get("type", ""), "Application form preview")
                            await cl.Message(
                                content=f"Screenshot — {shot_label}:",
                                elements=[
                                    cl.Image(
                                        name="apply_screenshot.png",
                                        path=result["screenshot"],
                                        display="inline",
                                    )
                                ],
                            ).send()
                    # Persist job listings across turns so the next message can
                    # reference them without re-running the search.
                    if update.get("last_search_results"):
                        cl.user_session.set(
                            "last_search_results", update["last_search_results"]
                        )
                    # Persist tailored resume metadata across turns
                    if update.get("last_tailored_resume"):
                        cl.user_session.set(
                            "last_tailored_resume", update["last_tailored_resume"]
                        )
                    # Persist auto_apply phase so selector advances correctly
                    if "last_apply_phase" in update:
                        phase = update["last_apply_phase"]
                        if phase:
                            cl.user_session.set("last_apply_phase", phase)
                        else:
                            cl.user_session.set("last_apply_phase", None)

                elif node == "synthesize":
                    text = update.get("final_response", "")
                    if text:
                        reply.content = text
                        await reply.update()
                        got_response = True

    except Exception as exc:
        reply.content = f"Sorry, something went wrong: {exc}"
        await reply.update()
        got_response = True
    finally:
        done = True
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await stream.aclose()

    if not got_response:
        reply.content = "I ran into an issue and couldn't generate a response. Please try again."
        await reply.update()


# ── HITL ──────────────────────────────────────────────────────────────────────

async def _handle_hitl(interrupt_update: Any, config: dict) -> None:
    items = interrupt_update if isinstance(interrupt_update, (list, tuple)) else [interrupt_update]
    payload: dict = {}
    for item in items:
        val = getattr(item, "value", item)
        if isinstance(val, dict) and val.get("type") == "hitl_request":
            payload = val
            break

    gate    = payload.get("gate") or payload.get("action", "action")
    details = payload.get("details", {})
    preview = payload.get("preview", "Please review the pending action.")

    # For auto_apply and submit_application gates, summarise what's about to happen
    if gate in ("auto_apply", "submit_application"):
        args = details.get("args", {})
        company = args.get("company", "")
        role    = args.get("role", "")
        url     = args.get("url", "")
        if company and role:
            preview = (
                f"**{gate.replace('_', ' ').title()}** for **{role}** at **{company}**\n\n"
                f"URL: {url}\n\n"
                "Approve to proceed, Edit to change args, or Reject to cancel."
            )

    res = await cl.AskActionMessage(
        content=f"**Approval required** — `{gate}`\n\n{preview}",
        actions=[
            cl.Action(name="approve", payload={"decision": "approve"}, label="Approve"),
            cl.Action(name="edit",    payload={"decision": "edit"},    label="Edit"),
            cl.Action(name="reject",  payload={"decision": "reject"},  label="Reject"),
        ],
        timeout=300,
    ).send()

    decision = "reject"
    correction: Optional[str] = None

    if res:
        decision = res["payload"].get("decision", "reject")
        if decision == "edit":
            edit_res = await cl.AskUserMessage(
                content="Enter your correction:", timeout=300
            ).send()
            correction = edit_res.get("output") if edit_res else None

    await _run_agent(
        Command(resume={"decision": decision, "correction": correction}),
        config,
    )


# ── Resume upload ─────────────────────────────────────────────────────────────

async def _handle_resume_upload(element: cl.Element, user_id: str) -> None:
    import aiofiles  # noqa: PLC0415

    msg = cl.Message(content="Ingesting your resume...")
    await msg.send()

    async with aiofiles.open(element.path, "rb") as fh:
        raw_bytes = await fh.read()

    log: list[str] = []
    async for event in ingest(user_id, element.name, raw_bytes):
        etype = event.get("type")
        if etype == "progress":
            log.append(f"- {event['step']}")
            msg.content = "\n".join(log)
            await msg.update()
        elif etype == "error":
            log.append(f"Error: {event['message']}")
            msg.content = "\n".join(log)
            await msg.update()
            return
        elif etype == "done":
            chunks = event.get("chunks", 0)
            facts = len(event.get("facts", []))
            log.append(f"\nDone — {chunks} chunks indexed, {facts} career facts extracted.")
            msg.content = "\n".join(log)
            await msg.update()

    await cl.Message(
        content=(
            "Your ATS-safe master resume is ready! "
            "Click a prompt below to get started, or type your own:"
        ),
        actions=_EXAMPLE_ACTIONS,
    ).send()


# ── Dashboard ─────────────────────────────────────────────────────────────────

def _maybe_store_workday_credentials(user_id: str, message: str) -> None:
    """If the user's message contains Workday credentials as JSON, store them."""
    import re
    try:
        # Look for a JSON object anywhere in the message
        match = re.search(r'\{[^{}]*"workday_email"[^{}]*\}', message)
        if not match:
            return
        data = json.loads(match.group())
        email = data.get("workday_email", "").strip()
        password = data.get("workday_password", "").strip()
        if email and password:
            sqlite.upsert_workday_credentials(user_id, email, password)
    except Exception:
        pass


async def _show_dashboard(user_id: str) -> None:
    apps = sqlite.list_applications(user_id)
    versions = sqlite.list_resume_versions(user_id)

    lines: list[str] = ["### Applications\n"]
    if apps:
        lines.append("| Company | Role | Status | Applied |")
        lines.append("| --- | --- | --- | --- |")
        for a in apps:
            date = str(a.get("applied_at", ""))[:10]
            lines.append(f"| {a['company']} | {a['role']} | {a['status']} | {date} |")
    else:
        lines.append("No applications tracked yet.")

    lines.append("\n### Resume Versions\n")
    if versions:
        lines.append("| Version | File | Uploaded |")
        lines.append("| --- | --- | --- |")
        for v in versions:
            date = str(v.get("uploaded_at", ""))[:10]
            lines.append(f"| {v['version']} | {v['filename']} | {date} |")
    else:
        lines.append("No resume versions yet.")

    await cl.Message(content="\n".join(lines)).send()
