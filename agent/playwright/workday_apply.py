"""Workday application automation — browser-use Agent.

The Agent handles all Workday form interaction autonomously using LLM + vision:

  fill phase   — navigate, sign in, upload resume, fill all steps, stop at Review
  submit phase — reopen session, click Submit, confirm

browser-use docs: https://github.com/browser-use/browser-use
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ── Task prompts ───────────────────────────────────────────────────────────────

def _profile_text(profile: dict) -> str:
    c = profile.get("contact", {}) or {}
    lines = []
    if c.get("name"):     lines.append(f"Full Name: {c['name']}")
    if c.get("phone"):    lines.append(f"Phone: {c['phone']}")
    if c.get("location"): lines.append(f"Location: {c['location']}")
    if c.get("linkedin"): lines.append(f"LinkedIn URL: {c['linkedin']}")
    if c.get("website"):  lines.append(f"Website: {c['website']}")
    return "\n".join(lines) if lines else "(see uploaded resume)"


def fill_task(url: str, profile: dict, resume_path: str) -> str:
    return f"""
Fill a Workday job application — stop before submitting.

Job URL: {url}

Follow these steps in order:

1. Navigate to the job URL. Click "Apply Now" or "Apply" if a button is visible.

2. Sign-in screen: click "Sign in with email", then enter
   email: <workday_email>  and  password: <workday_password>
   If the account doesn't exist, click "Create Account" and register with those same credentials.
   Wait for email verification if required (look for a verification prompt).

3. "Autofill with Resume" step: upload the file at this path: {resume_path}
   Wait up to 15 seconds for Workday to finish parsing the PDF before clicking Next.

4. "My Information" step — fill with the candidate profile below.
   Skip fields already populated by the resume autofill.
{_profile_text(profile)}
   Set phone device type to "Mobile".

5. "My Experience" step — check fields are pre-filled from resume upload.
   Fill any empty required fields using profile data. Do not remove existing entries.

6. "Application Questions" step — answer every visible question:
   - Authorized / eligible to work in this country → Yes
   - Require visa sponsorship → No
   - Any other Yes / No eligibility questions → Yes
   - Dropdown questions → pick the first sensible non-empty option
   - Text questions → leave blank if optional

7. "Voluntary Disclosures" step — choose "Prefer Not to Answer" for every question.

8. "Self Identify" step — choose "Prefer Not to Answer" for every question.

9. Click "Save and Continue" after completing each step to advance.

10. STOP as soon as you reach the "Review" page. Do NOT click Submit.

When finished respond with exactly: reached_review
If you cannot complete a step, respond with: error: <brief reason>
""".strip()


def submit_task(url: str) -> str:
    return f"""
Submit the Workday job application.

Application URL: {url}

Steps:
1. Navigate to the URL.
2. If you see a sign-in screen, click "Sign in with email" and sign in using
   email: <workday_email>  and  password: <workday_password>
3. You should land on (or navigate to) the Review page of the application.
4. Click the Submit button to submit the application.
5. Wait for the confirmation page ("Thank you" / "Application submitted").

When finished respond with exactly: submitted
If submission fails, respond with: error: <brief reason>
""".strip()


# ── Browser-use helpers ────────────────────────────────────────────────────────

def _make_llm():
    from browser_use.llm.openai.chat import ChatOpenAI
    return ChatOpenAI(
        model="gpt-4o",
        temperature=0.0,
        api_key=os.getenv("OPENAI_API_KEY"),
    )


def _make_session(headless: bool) -> "BrowserSession":
    from browser_use import BrowserSession
    return BrowserSession(
        headless=headless,
        minimum_wait_page_load_time=2.0,
        wait_between_actions=1.2,
        highlight_elements=False,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def _take_screenshot_via_session(session: Any, shot_path: str) -> None:
    """Take a screenshot using the already-open browser session."""
    try:
        Path(shot_path).parent.mkdir(parents=True, exist_ok=True)
        page = await session.get_current_page()
        await page.screenshot(path=shot_path, full_page=True)
        log.info("[workday_apply] screenshot saved: %s", shot_path)
    except Exception as exc:
        log.warning("[workday_apply] screenshot failed: %s", exc)


async def run_fill_agent(
    url: str,
    profile: dict,
    resume_path: str,
    credentials: dict,
    headless: bool,
    shot_path: str,
    max_steps: int = 40,
) -> dict[str, Any]:
    """Run the browser-use Agent to fill all Workday steps up to Review.

    Returns a dict with keys: type, ready_to_submit, screenshot, result, error.
    """
    from browser_use import Agent

    task      = fill_task(url, profile, resume_path)
    session   = _make_session(headless)
    sensitive = {k: v for k, v in credentials.items() if v}

    log.info("[workday_apply] run_fill_agent start url=%s headless=%s has_creds=%s resume=%s",
             url, headless, bool(sensitive), resume_path)

    agent = Agent(
        task=task,
        llm=_make_llm(),
        browser_session=session,
        sensitive_data=sensitive or None,
        available_file_paths=[resume_path] if resume_path and Path(resume_path).exists() else None,
        max_actions_per_step=5,
        use_vision=True,
        max_failures=3,
    )

    try:
        history = await agent.run(max_steps=max_steps)
        final   = history.final_result() or ""
        log.info("[workday_apply] run_fill_agent finished final_result=%r", final[:200])
        reached = "reached_review" in final.lower()
        err     = final if "error:" in final.lower() else None

        await _take_screenshot_via_session(session, shot_path)

        return {
            "type":            "on_review" if reached else "completed",
            "ready_to_submit": reached,
            "screenshot":      shot_path,
            "result":          final[:500],
            "error":           err,
        }
    except Exception as exc:
        log.exception("[workday_apply] run_fill_agent raised: %s", exc)
        return {
            "type":            "error",
            "ready_to_submit": False,
            "screenshot":      shot_path,
            "error":           str(exc),
        }
    finally:
        try:
            await session.stop()
        except Exception:
            pass


async def run_submit_agent(
    url: str,
    credentials: dict,
    headless: bool,
    max_steps: int = 10,
) -> dict[str, Any]:
    """Run the browser-use Agent to submit the already-filled application."""
    from browser_use import Agent

    task      = submit_task(url)
    session   = _make_session(headless)
    sensitive = {k: v for k, v in credentials.items() if v}

    log.info("[workday_apply] run_submit_agent start url=%s headless=%s has_creds=%s",
             url, headless, bool(sensitive))

    agent = Agent(
        task=task,
        llm=_make_llm(),
        browser_session=session,
        sensitive_data=sensitive or None,
        max_actions_per_step=3,
        use_vision=True,
        max_failures=3,
    )

    try:
        history   = await agent.run(max_steps=max_steps)
        final     = history.final_result() or ""
        log.info("[workday_apply] run_submit_agent finished final_result=%r", final[:200])
        submitted = "submitted" in final.lower()
        return {
            "type":   "submitted" if submitted else "unknown_state",
            "result": final[:300],
        }
    except Exception as exc:
        log.exception("[workday_apply] run_submit_agent raised: %s", exc)
        return {"type": "error", "message": str(exc)}
    finally:
        try:
            await session.stop()
        except Exception:
            pass
