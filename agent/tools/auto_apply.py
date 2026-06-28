"""Auto-apply tool.

Drives Playwright to fill (and optionally submit) a job application form.
HITL-gated twice:

  Gate 1 (``auto_apply``)         → before any field is touched
  Gate 2 (``submit_application``) → before clicking Submit

Workday flow is special-cased:
  - Uses a persistent browser context (profile saved per user) so login
    sessions survive between tool calls.
  - If the login page is detected and no stored credentials are available,
    the tool opens a visible (non-headless) browser, streams a message to
    the user, and polls up to 120 s for the user to log in.
  - Credentials can optionally be stored in SQLite for future reuse.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.playwright import ats_detector
from agent.playwright.form_filler import FormFiller, build_fill_plan
from db import sqlite

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Automatically fill and submit a job application using the user's tailored "
    "resume. Pauses for approval before filling and before submitting. "
    "Requires: url (job application URL), company, role, resume_path "
    "(path to tailored PDF). These come from last_tailored_resume and "
    "last_search_results — always pass them explicitly."
)

SCREENSHOT_DIR = Path(os.getenv("RESUMES_DIR", "resumes")) / "screenshots"


class Phase(str, Enum):
    PLAN = "plan"
    FILL = "fill"
    SUBMIT = "submit"


class ToolSchema(BaseModel):
    url: Optional[str] = Field(None, description="Application page URL.")
    company: Optional[str] = Field(None)
    role: Optional[str] = Field(None)
    resume_path: Optional[str] = Field(
        None, description="Path to the tailored resume PDF to upload."
    )
    phase: Phase = Field(Phase.PLAN, description="plan | fill | submit")
    headless: bool = Field(True)
    workday_email: Optional[str] = Field(
        None, description="Workday account email (if user wants auto-login)."
    )
    workday_password: Optional[str] = Field(
        None, description="Workday account password (if user wants auto-login)."
    )


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    log.info("[auto_apply] run called: phase=%s url=%s company=%s role=%s",
             input.get("phase"), input.get("url"), input.get("company"), input.get("role"))
    try:
        args = ToolSchema(**input)
    except Exception as exc:
        log.error("[auto_apply] invalid args: %s", exc)
        return {"tool": "auto_apply", "error": "invalid_args", "message": str(exc)}

    if not args.url:
        return {
            "tool": "auto_apply",
            "error": "missing_url",
            "message": (
                "Job application URL is required. "
                "It should come from last_tailored_resume.url or last_search_results."
            ),
        }
    if not args.company or not args.role:
        return {
            "tool": "auto_apply",
            "error": "missing_company_role",
            "message": "Both company and role are required. Use values from last_tailored_resume.",
        }

    handlers = {
        Phase.PLAN: _plan,
        Phase.FILL: _fill,
        Phase.SUBMIT: _submit,
    }
    return await handlers[args.phase](args, user_id)


# ── Phase: PLAN ──────────────────────────────────────────────────────────────

async def _plan(args: ToolSchema, user_id: str) -> dict[str, Any]:
    platform = ats_detector.detect_from_url(args.url) or "generic"
    profile_row = sqlite.get_current_resume(user_id)
    if not profile_row:
        return {"tool": "auto_apply", "error": "onboarding_required",
                "message": "Upload a resume before applying."}
    plan = build_fill_plan(profile_row["data"], platform)
    has_creds = bool(sqlite.get_workday_credentials(user_id)) if platform == "workday" else False
    return {
        "tool": "auto_apply",
        "phase": Phase.PLAN.value,
        "ats_platform": platform,
        "plan": [{"field": p["field"], "value": p["value"], "type": p["type"]} for p in plan],
        "url": args.url,
        "workday_has_saved_credentials": has_creds,
        "note": (
            "Workday requires a logged-in account. "
            "If you have Workday credentials, the tool will sign in automatically. "
            "Otherwise it will open a visible browser for you to log in."
        ) if platform == "workday" else None,
    }


# ── Phase: FILL ──────────────────────────────────────────────────────────────

async def _fill(args: ToolSchema, user_id: str) -> dict[str, Any]:
    profile_row = sqlite.get_current_resume(user_id)
    if not profile_row:
        return {"tool": "auto_apply", "error": "onboarding_required"}

    platform = ats_detector.detect_from_url(args.url) or "generic"

    # Store credentials if the user passed them this turn
    if args.workday_email and args.workday_password:
        sqlite.upsert_workday_credentials(user_id, args.workday_email, args.workday_password)

    if platform == "workday":
        return await _workday_fill(args, user_id, profile_row["data"])

    # Generic / non-Workday ATS
    async with FormFiller(headless=args.headless) as filler:
        opened = await filler.open(args.url)
        if opened["type"] in ("captcha_blocked", "login_required"):
            return {"tool": "auto_apply", **opened}

        platform = await filler.detect_platform(args.url)
        plan = build_fill_plan(profile_row["data"], platform)
        fill_result = await filler.fill(plan, args.resume_path)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shot = SCREENSHOT_DIR / f"{user_id}_{args.company}_{args.role}.png".replace(" ", "-")
        await filler.screenshot(str(shot))

        return {
            "tool": "auto_apply",
            "phase": Phase.FILL.value,
            "ats_platform": platform,
            "screenshot": str(shot),
            **fill_result,
        }


async def _workday_fill(args: ToolSchema, user_id: str, profile: dict) -> dict[str, Any]:
    """Full Workday application flow using browser-use Agent."""
    log.info("[auto_apply] _workday_fill start url=%s headless=%s resume=%s",
             args.url, args.headless, args.resume_path)
    from agent.playwright.workday_apply import run_fill_agent

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # Merge stored credentials with anything passed this turn
    creds_row = sqlite.get_workday_credentials(user_id) or {}
    credentials = {
        "workday_email":    args.workday_email    or creds_row.get("email", ""),
        "workday_password": args.workday_password or creds_row.get("password_enc", ""),
    }

    shot_path = str(
        SCREENSHOT_DIR / f"{user_id}_{args.company}_{args.role}.png".replace(" ", "-")
    )

    result = await run_fill_agent(
        url=args.url,
        profile=profile,
        resume_path=args.resume_path or "",
        credentials=credentials,
        headless=args.headless,
        shot_path=shot_path,
        max_steps=40,
    )

    log.info("[auto_apply] _workday_fill result: %s", result)
    return {
        "tool": "auto_apply",
        "phase": Phase.FILL.value,
        "ats_platform": "workday",
        "url": args.url,
        **result,
    }


# ── Phase: SUBMIT ─────────────────────────────────────────────────────────────

async def _submit(args: ToolSchema, user_id: str) -> dict[str, Any]:
    platform = ats_detector.detect_from_url(args.url) or "generic"

    if platform == "workday":
        return await _workday_submit(args, user_id)

    async with FormFiller(headless=args.headless) as filler:
        opened = await filler.open(args.url)
        if opened["type"] in ("captcha_blocked", "login_required"):
            return {"tool": "auto_apply", **opened}
        result = await filler.submit()

    if result["type"] == "submitted":
        _record_application(args, user_id)
        return {
            "tool": "auto_apply",
            "phase": Phase.SUBMIT.value,
            "applied": True,
            "company": args.company,
            "role": args.role,
        }
    return {"tool": "auto_apply", "phase": Phase.SUBMIT.value, "applied": False, **result}


async def _workday_submit(args: ToolSchema, user_id: str) -> dict[str, Any]:
    from agent.playwright.workday_apply import run_submit_agent

    creds_row = sqlite.get_workday_credentials(user_id) or {}
    credentials = {
        "workday_email":    args.workday_email    or creds_row.get("email", ""),
        "workday_password": args.workday_password or creds_row.get("password_enc", ""),
    }

    result = await run_submit_agent(
        url=args.url,
        credentials=credentials,
        headless=args.headless,
        max_steps=10,
    )

    if result.get("type") == "submitted":
        _record_application(args, user_id)
        return {
            "tool": "auto_apply",
            "phase": Phase.SUBMIT.value,
            "applied": True,
            "company": args.company,
            "role": args.role,
        }
    return {"tool": "auto_apply", "phase": Phase.SUBMIT.value, "applied": False, **result}


def _record_application(args: ToolSchema, user_id: str) -> None:
    sqlite.create_application(
        user_id=user_id,
        company=args.company or "",
        role=args.role or "",
        url=args.url,
        status="applied",
        tailored_resume_path=args.resume_path,
    )
