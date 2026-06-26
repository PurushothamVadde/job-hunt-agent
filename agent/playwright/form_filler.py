"""Playwright form filler for ATS application pages.

Responsibilities:
- Launch a (stealthy) Chromium browser
- Detect the ATS platform and resolve the field map
- Build a fill plan from the candidate profile (no writes until approved)
- Fill fields with human-like delays
- Detect captcha / login walls and surface them as structured results
- Capture a screenshot for HITL review and submit on approval

All blocking Playwright calls run inside async methods so the API event loop is
never blocked. Errors are returned as structured dicts, never raised, so the
auto-apply pipeline can stream them as SSE events.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Optional

from agent.playwright import ats_detector
from agent.playwright.field_maps import generic, get_field_map


def build_fill_plan(profile: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    """Map canonical profile fields to selectors + values for ``platform``.

    Returns a list of ``{"field", "selector", "value", "type"}`` dicts. For the
    ``generic`` platform selectors are left ``None`` and resolved at fill time
    via label heuristics.
    """
    contact = profile.get("contact", {}) or {}
    name = (contact.get("name") or "").strip()
    parts = name.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""

    values = {
        "First Name": name if platform == "lever" else first,
        "Last Name": last,
        "Email": contact.get("email", ""),
        "Phone": contact.get("phone", ""),
        "LinkedIn": contact.get("linkedin", ""),
    }

    field_map = get_field_map(platform)
    plan: list[dict[str, Any]] = []
    for field, value in values.items():
        if not value:
            continue
        selector = field_map.get(field) if field_map else None
        # Lever merges last name into first; skip explicit Last Name.
        if platform == "lever" and field == "Last Name":
            continue
        plan.append(
            {"field": field, "selector": selector, "value": value, "type": "text"}
        )
    # Resume file upload.
    plan.append(
        {
            "field": "Resume",
            "selector": (field_map or {}).get("Resume", "input[type='file']"),
            "value": None,
            "type": "file",
        }
    )
    return plan


async def _human_delay() -> None:
    await asyncio.sleep(random.uniform(1.0, 3.0))


async def _detect_walls(page) -> Optional[dict[str, Any]]:
    """Detect captcha / login walls. Returns an SSE-style event dict or None."""
    try:
        html = (await page.content()).lower()
    except Exception:
        return None
    if any(k in html for k in ("recaptcha", "hcaptcha", "g-recaptcha", "captcha")):
        return {"type": "captcha_blocked"}
    if any(k in html for k in ("sign in", "log in to apply", "please log in")):
        return {"type": "login_required", "url": page.url}
    return None


async def _resolve_generic_selectors(page, plan: list[dict[str, Any]]) -> None:
    """Fill in ``selector`` for generic-platform plan items via label text."""
    try:
        labels = await page.query_selector_all("label")
    except Exception:
        labels = []
    label_to_selector: dict[str, str] = {}
    for label in labels:
        try:
            text = await label.inner_text()
            for_attr = await label.get_attribute("for")
        except Exception:
            continue
        field = generic.classify_label(text)
        if field and for_attr:
            label_to_selector[field] = f"#{for_attr}"
    for item in plan:
        if item["selector"] is None and item["field"] in label_to_selector:
            item["selector"] = label_to_selector[item["field"]]


class FormFiller:
    """Async context-manager wrapping a Playwright browser session."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self) -> "FormFiller":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        try:
            from playwright_stealth import stealth_async

            await stealth_async(self.page)
        except Exception:
            pass  # stealth is best-effort
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for closer in (self.context, self.browser):
            try:
                if closer:
                    await closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def open(self, url: str) -> dict[str, Any]:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.0)
        wall = await _detect_walls(self.page)
        if wall:
            return wall
        return {"type": "opened", "url": self.page.url}

    async def detect_platform(self, url: str) -> str:
        return await ats_detector.detect_on_page(self.page, url)

    async def fill(
        self, plan: list[dict[str, Any]], resume_path: Optional[str]
    ) -> dict[str, Any]:
        """Fill all planned fields with human-like delays."""
        await _resolve_generic_selectors(self.page, plan)
        filled, skipped = [], []
        for item in plan:
            selector = item["selector"]
            if not selector:
                skipped.append(item["field"])
                continue
            try:
                if item["type"] == "file":
                    if resume_path and Path(resume_path).exists():
                        await self.page.set_input_files(selector, resume_path)
                        filled.append(item["field"])
                    else:
                        skipped.append(item["field"])
                else:
                    await self.page.fill(selector, item["value"])
                    filled.append(item["field"])
                await _human_delay()
            except Exception:
                skipped.append(item["field"])
        return {"type": "filled", "filled": filled, "skipped": skipped}

    async def screenshot(self, out_path: str) -> str:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=out_path, full_page=True)
        return out_path

    async def submit(
        self, submit_selector: str = "button[type='submit']"
    ) -> dict[str, Any]:
        wall = await _detect_walls(self.page)
        if wall:
            return wall
        try:
            await self.page.click(submit_selector, timeout=10000)
            await asyncio.sleep(2.0)
            return {"type": "submitted"}
        except Exception as exc:
            return {"type": "unknown_form", "error": str(exc)}
