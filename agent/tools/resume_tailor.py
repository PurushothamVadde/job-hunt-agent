"""Resume tailoring tool.

Gap-analyses a job description against the user's current resume profile, builds
a tailored canonical profile, and (after the HITL ``write_resume`` gate) renders
an ATS-safe PDF to disk.

Accepts either:
  - ``jd_text``  — full job-description text (preferred)
  - ``job_url``  — URL to a job posting; the tool fetches and extracts the text
                   using Crawl4AI so JS-rendered pages (Phenom/CVS, Workday, etc.)
                   are handled correctly.

NOTE: this tool is HITL-gated. The graph calls ``interrupt()`` before the PDF is
written. The ``run`` signature accepts ``approved`` so callers can perform the
analysis phase without writing, then re-invoke with ``approved=True`` to commit.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.resume import tailoring
from agent.resume.pdf_generator import generate_ats_pdf
from db import sqlite

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Analyse a job description against the user's resume (matched / missing "
    "skills) and produce a tailored, ATS-safe resume PDF. Provide either "
    "``jd_text`` (the full job-description text) or ``job_url`` (the URL of "
    "the job posting — the tool will fetch it automatically). Requires approval "
    "before writing the file."
)

RESUMES_DIR = Path(os.getenv("RESUMES_DIR", "resumes"))


class ToolSchema(BaseModel):
    jd_text: Optional[str] = Field(None, description="Full job-description text.")
    job_url: Optional[str] = Field(None, description="URL of the job posting to fetch.")
    company: Optional[str] = Field(None)
    role: Optional[str] = Field(None)
    approved: bool = Field(
        False, description="Set True after HITL approval to write the PDF."
    )


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)

    # Resolve job description text
    jd_text = (args.jd_text or "").strip()
    if not jd_text and args.job_url:
        logger.info("[resume_tailor] fetching jd from url=%s", args.job_url)
        jd_text = await _fetch_jd(args.job_url)
        if not jd_text:
            return {
                "tool": "resume_tailor",
                "error": "jd_fetch_failed",
                "message": (
                    f"Could not retrieve the job description from {args.job_url}. "
                    "Please paste the job description text directly."
                ),
            }

    if not jd_text:
        return {
            "tool": "resume_tailor",
            "error": "jd_missing",
            "message": "Provide either jd_text or a job_url to fetch from.",
        }

    profile_row = sqlite.get_current_resume(user_id)
    if not profile_row:
        return {
            "tool": "resume_tailor",
            "error": "onboarding_required",
            "message": "No resume profile found; upload a master resume first.",
        }
    profile = profile_row["data"]

    gaps = await tailoring.gap_analysis(jd_text, profile)
    tailored_profile = await tailoring.tailor(profile, jd_text)

    result: dict[str, Any] = {
        "tool": "resume_tailor",
        "matched": gaps["matched"],
        "missing_required": gaps["missing_required"],
        "missing_preferred": gaps["missing_preferred"],
        "keywords": gaps["keywords"],
        "tailored_profile": tailored_profile,
        "company": args.company,
        "role": args.role,
        "job_url": args.job_url,
    }

    if not args.approved:
        result["resume_ready"] = False
        return result

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "_".join(filter(None, [user_id, args.company, args.role])).replace(" ", "-")
    pdf_path = RESUMES_DIR / f"{safe or user_id}_tailored.pdf"
    try:
        generate_ats_pdf(tailored_profile, pdf_path)
        result["resume_ready"] = True
        result["path"] = str(pdf_path)
    except Exception as exc:
        result["resume_ready"] = False
        result["error"] = f"pdf_generation_failed: {exc}"
    return result


# ── Job description fetcher ─────────────────────────────────────────────────────

async def _fetch_jd(url: str) -> str:
    """Fetch job description text from a job posting URL.

    Tries Crawl4AI first (handles JS-rendered SPAs like Phenom/CVS, Workday).
    Falls back to plain httpx + BeautifulSoup for static pages.
    """
    text = await _fetch_crawl4ai(url)
    if len(text) > 200:
        return text
    text = await _fetch_httpx(url)
    return text


async def _fetch_crawl4ai(url: str) -> str:
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
    except Exception:
        return ""

    try:
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        run_cfg = CrawlerRunConfig(
            delay_before_return_html=5.0,
            remove_overlay_elements=True,
            word_count_threshold=0,
            page_timeout=30000,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
            inner = result._results[0] if hasattr(result, "_results") and result._results else result
            html = getattr(inner, "html", "") or ""
            md = getattr(inner, "markdown", "")
            markdown = md if isinstance(md, str) else str(md or "")

        # Prefer markdown (cleaner text); fall back to stripping HTML
        text = markdown if len(markdown) > 200 else _strip_html(html)
        return _truncate(text)
    except Exception as exc:
        logger.warning("[resume_tailor] crawl4ai fetch failed url=%s err=%s", url, exc)
        return ""


async def _fetch_httpx(url: str) -> str:
    try:
        import httpx
        from bs4 import BeautifulSoup
    except Exception:
        return ""

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20.0,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            html = r.text
        return _truncate(_strip_html(html))
    except Exception as exc:
        logger.warning("[resume_tailor] httpx fetch failed url=%s err=%s", url, exc)
        return ""


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _truncate(text: str, max_chars: int = 12000) -> str:
    return text[:max_chars]
