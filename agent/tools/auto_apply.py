"""Auto-apply tool.

Drives Playwright to fill (and optionally submit) a job application form. This
tool is HITL-gated twice by the graph:

  Gate 1 (``auto_apply``)        -> before any field is touched
  Gate 2 (``submit_application``)-> before clicking Submit

The graph drives these gates; this ``run`` accepts a ``phase`` argument so it can
be re-entered after each approval:

  phase="plan"   -> detect ATS + build fill plan (no browser writes)
  phase="fill"   -> open page, fill fields, capture screenshot
  phase="submit" -> click submit and record the application
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.playwright import ats_detector
from agent.playwright.form_filler import FormFiller, build_fill_plan
from db import sqlite

DESCRIPTION = (
    "Automatically fill and submit a job application using the user's tailored "
    "resume. Pauses for approval before filling and before submitting."
)

SCREENSHOT_DIR = Path(os.getenv("RESUMES_DIR", "resumes")) / "screenshots"


class ToolSchema(BaseModel):
    url: str = Field(..., description="Application page URL.")
    company: str = Field(...)
    role: str = Field(...)
    resume_path: Optional[str] = Field(
        None, description="Path to the tailored resume PDF to upload."
    )
    phase: str = Field("plan", description="plan | fill | submit")
    headless: bool = Field(True)


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)

    if args.phase == "plan":
        platform = ats_detector.detect_from_url(args.url) or "generic"
        profile_row = sqlite.get_current_resume(user_id)
        if not profile_row:
            return {
                "tool": "auto_apply",
                "error": "onboarding_required",
                "message": "Upload a resume before applying.",
            }
        plan = build_fill_plan(profile_row["data"], platform)
        return {
            "tool": "auto_apply",
            "phase": "plan",
            "ats_platform": platform,
            "plan": [
                {"field": p["field"], "value": p["value"], "type": p["type"]}
                for p in plan
            ],
            "url": args.url,
        }

    if args.phase == "fill":
        return await _fill(args, user_id)

    if args.phase == "submit":
        return await _submit(args, user_id)

    return {"tool": "auto_apply", "error": f"unknown phase '{args.phase}'"}


async def _fill(args: ToolSchema, user_id: str) -> dict[str, Any]:
    profile_row = sqlite.get_current_resume(user_id)
    if not profile_row:
        return {"tool": "auto_apply", "error": "onboarding_required"}

    async with FormFiller(headless=args.headless) as filler:
        opened = await filler.open(args.url)
        if opened["type"] in ("captcha_blocked", "login_required"):
            return {"tool": "auto_apply", **opened}

        platform = await filler.detect_platform(args.url)
        plan = build_fill_plan(profile_row["data"], platform)
        fill_result = await filler.fill(plan, args.resume_path)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shot = SCREENSHOT_DIR / f"{user_id}_{args.company}_{args.role}.png".replace(
            " ", "-"
        )
        await filler.screenshot(str(shot))

        return {
            "tool": "auto_apply",
            "phase": "fill",
            "ats_platform": platform,
            "screenshot": str(shot),
            **fill_result,
        }


async def _submit(args: ToolSchema, user_id: str) -> dict[str, Any]:
    async with FormFiller(headless=args.headless) as filler:
        opened = await filler.open(args.url)
        if opened["type"] in ("captcha_blocked", "login_required"):
            return {"tool": "auto_apply", **opened}
        result = await filler.submit()

    if result["type"] == "submitted":
        sqlite.create_application(
            user_id=user_id,
            company=args.company,
            role=args.role,
            url=args.url,
            status="applied",
            tailored_resume_path=args.resume_path,
        )
        return {
            "tool": "auto_apply",
            "phase": "submit",
            "applied": True,
            "company": args.company,
            "role": args.role,
        }
    return {"tool": "auto_apply", "phase": "submit", "applied": False, **result}
