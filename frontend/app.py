"""Gradio UI for JobHuntAI.

Tabs:
  * Account     — login / register; the JWT is stored in a gr.State object.
  * Onboarding  — master resume upload (streams ingestion progress).
  * Chat        — streaming chat with the agent (SSE via httpx).
  * Dashboard   — application tracker + resume versions.

Run with:  python -m frontend.app   (after starting the API server).
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

import gradio as gr
import httpx

from frontend import API_BASE, dashboard, onboarding


# --------------------------------------------------------------------------- #
# Chat streaming
# --------------------------------------------------------------------------- #
def stream_chat(
    message: str,
    history: list[dict[str, str]],
    token: Optional[str],
    session_id: Optional[str],
) -> Iterator[tuple[list[dict[str, str]], Optional[str]]]:
    history = history or []
    if not token:
        history.append({"role": "user", "content": message})
        history.append(
            {"role": "assistant", "content": "Please log in on the Account tab first."}
        )
        yield history, session_id
        return

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    assistant = ""
    body = {"message": message}
    if session_id:
        body["session_id"] = session_id

    try:
        with httpx.stream(
            "POST",
            f"{API_BASE}/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=300.0,
        ) as resp:
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):].strip())
                assistant, session_id = _apply_event(event, assistant, session_id)
                history[-1]["content"] = assistant
                yield history, session_id
    except Exception as exc:
        history[-1]["content"] = assistant + f"\n\n[stream error: {exc}]"
        yield history, session_id


def _apply_event(
    event: dict[str, Any], assistant: str, session_id: Optional[str]
) -> tuple[str, Optional[str]]:
    etype = event.get("type")
    if etype == "token":
        assistant += event.get("content", "")
    elif etype == "progress":
        assistant += f"\n_{event.get('step')}_\n"
    elif etype == "tool_start":
        assistant += f"\n[running {event.get('tool')}]\n"
    elif etype == "hitl_request":
        assistant += (
            f"\n**Approval needed** for `{event.get('action')}`. "
            "Use the Approve controls below.\n"
        )
    elif etype == "resume_ready":
        assistant += f"\nTailored resume ready: {event.get('path')}\n"
    elif etype == "applied":
        assistant += f"\nApplied to {event.get('company')} — {event.get('role')}.\n"
    elif etype == "captcha_blocked":
        assistant += "\nBlocked by CAPTCHA — manual step required.\n"
    elif etype == "login_required":
        assistant += f"\nLogin required at {event.get('url')}.\n"
    elif etype == "onboarding_required":
        assistant += f"\n{event.get('message')}\n"
    return assistant, session_id


def send_approval(token: str, session_id: str, decision: str, correction: str) -> str:
    if not token or not session_id:
        return "Need a logged-in session with a pending request."
    try:
        with httpx.stream(
            "POST",
            f"{API_BASE}/chat/approve",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "session_id": session_id,
                "decision": decision,
                "correction": correction or None,
            },
            timeout=300.0,
        ) as resp:
            out = []
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    out.append(line[len("data:"):].strip())
            return "Resumed. " + " ".join(out[-3:])
    except Exception as exc:
        return f"Error: {exc}"


# --------------------------------------------------------------------------- #
# UI assembly
# --------------------------------------------------------------------------- #
def build_app() -> gr.Blocks:
    with gr.Blocks(title="JobHuntAI") as demo:
        token_state = gr.State(None)
        session_state = gr.State(None)

        gr.Markdown("# JobHuntAI\nYour multi-session AI job-search assistant.")

        with gr.Tab("Account"):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Login")
                    li_user = gr.Textbox(label="Username")
                    li_pass = gr.Textbox(label="Password", type="password")
                    li_btn = gr.Button("Login", variant="primary")
                with gr.Column():
                    gr.Markdown("### Register")
                    rg_user = gr.Textbox(label="Username")
                    rg_email = gr.Textbox(label="Email")
                    rg_pass = gr.Textbox(label="Password", type="password")
                    rg_btn = gr.Button("Register")
            auth_status = gr.Markdown("")

            def _do_login(u, p):
                tok, msg = onboarding.login(u, p)
                return tok, msg

            def _do_register(u, e, p):
                tok, msg = onboarding.register(u, e, p)
                return tok, msg

            li_btn.click(_do_login, [li_user, li_pass], [token_state, auth_status])
            rg_btn.click(
                _do_register, [rg_user, rg_email, rg_pass], [token_state, auth_status]
            )

        with gr.Tab("Onboarding"):
            gr.Markdown("### Upload your master resume (PDF or DOCX)")
            resume_file = gr.File(label="Resume", type="filepath")
            upload_btn = gr.Button("Upload & Ingest", variant="primary")
            ingest_log = gr.Textbox(label="Ingestion progress", lines=10)

            upload_btn.click(
                onboarding.upload_resume,
                [token_state, resume_file],
                [ingest_log],
            )

        with gr.Tab("Chat"):
            chatbot = gr.Chatbot(label="JobHuntAI", type="messages", height=480)
            msg = gr.Textbox(label="Message", placeholder="Find me backend roles at Stripe...")
            send = gr.Button("Send", variant="primary")

            send.click(
                stream_chat,
                [msg, chatbot, token_state, session_state],
                [chatbot, session_state],
            ).then(lambda: "", None, [msg])
            msg.submit(
                stream_chat,
                [msg, chatbot, token_state, session_state],
                [chatbot, session_state],
            ).then(lambda: "", None, [msg])

            gr.Markdown("### Human-in-the-loop approval")
            with gr.Row():
                decision = gr.Radio(
                    ["approve", "edit", "reject"], value="approve", label="Decision"
                )
                correction = gr.Textbox(label="Correction (for 'edit')")
            approve_btn = gr.Button("Send decision")
            approve_status = gr.Markdown("")
            approve_btn.click(
                send_approval,
                [token_state, session_state, decision, correction],
                [approve_status],
            )

        with gr.Tab("Dashboard"):
            gr.Markdown("### Application tracker")
            refresh_btn = gr.Button("Refresh")
            apps_table = gr.Dataframe(
                headers=dashboard.APP_COLUMNS, label="Applications", interactive=False
            )
            with gr.Row():
                app_id_in = gr.Number(label="Application ID", precision=0)
                status_in = gr.Dropdown(
                    ["applied", "phone_screen", "interview", "offer", "rejected"],
                    label="New status",
                )
                update_btn = gr.Button("Update status")
            update_status = gr.Markdown("")

            gr.Markdown("### Resume versions")
            versions_table = gr.Dataframe(
                headers=dashboard.VERSION_COLUMNS,
                label="Resume versions",
                interactive=False,
            )

            refresh_btn.click(
                dashboard.fetch_applications, [token_state], [apps_table]
            )
            refresh_btn.click(
                dashboard.fetch_resume_versions, [token_state], [versions_table]
            )
            update_btn.click(
                dashboard.update_application_status,
                [token_state, app_id_in, status_in],
                [update_status],
            )

    return demo


def main() -> None:
    build_app().launch()


if __name__ == "__main__":
    main()
