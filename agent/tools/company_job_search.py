"""Company job-search tool.

Flow:
  1. DuckDuckGo → find official careers page URL
  2. Playwright → navigate, search, extract job listings
  3. GPT-4o → rank extracted jobs by fit against the user's resume
  4. Return top-K ranked jobs with fit scores for the user to choose from
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from agent.playwright.job_scraper import find_careers_url, scrape_jobs
from db import chroma, sqlite

DESCRIPTION = (
    "Browse a company's official careers page, search for roles matching the "
    "user's input (role + location + remote), then rank the top results by fit "
    "against the user's resume. Returns ranked job listings with fit scores for "
    "the user to select from."
)


# Well-known cities → country inference so the tool is resilient when the
# selector omits country (e.g. "Dallas" obviously implies USA).
_CITY_TO_COUNTRY: dict[str, str] = {
    "dallas": "USA", "houston": "USA", "chicago": "USA", "new york": "USA",
    "nyc": "USA", "los angeles": "USA", "seattle": "USA", "austin": "USA",
    "san francisco": "USA", "boston": "USA", "denver": "USA", "atlanta": "USA",
    "miami": "USA", "phoenix": "USA", "minneapolis": "USA", "portland": "USA",
    "london": "UK", "manchester": "UK", "birmingham": "UK",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "berlin": "Germany", "munich": "Germany",
    "paris": "France",
    "amsterdam": "Netherlands",
    "bangalore": "India", "hyderabad": "India", "mumbai": "India",
}


def _infer_country(city: Optional[str], country: Optional[str]) -> str:
    if country:
        return country
    if city:
        return _CITY_TO_COUNTRY.get(city.lower().strip(), "USA")
    return "USA"


class ToolSchema(BaseModel):
    company: str = Field(..., description="Company name, e.g. 'CVS Health'.")
    country: Optional[str] = Field(None, description="Country, e.g. 'USA'. Inferred from city if omitted.")
    city: Optional[str] = Field(None, description="City, state, or region within the country.")
    role: Optional[str] = Field(None, description="Target role / title keyword.")
    include_remote: bool = Field(True, description="Include remote/hybrid postings.")
    max_results: int = Field(8, ge=1, le=25)


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)
    country = _infer_country(args.city, args.country)

    logger.info("[job_search] company=%s role=%s city=%s country=%s",
                args.company, args.role, args.city, country)

    # Step 1: Find the careers page URL
    careers_url = await find_careers_url(args.company)
    logger.info("[job_search] careers_url=%s", careers_url)
    if not careers_url:
        return {
            "tool": "company_job_search",
            "company": args.company,
            "error": "Could not locate the careers page for this company.",
            "results": [],
        }

    # Step 2: Scrape jobs — fetch more than needed so ranking can trim down
    try:
        raw_jobs = await scrape_jobs(
            company=args.company,
            careers_url=careers_url,
            role=args.role or "software engineer",
            country=country,
            city=args.city,
            max_jobs=args.max_results * 3,
        )
    except RuntimeError as exc:
        logger.error("[job_search] scrape failed: %s", exc)
        return {
            "tool": "company_job_search",
            "company": args.company,
            "careers_url": careers_url,
            "error": str(exc),
            "message": (
                "Job search is temporarily unavailable because the browser runtime "
                "is not installed. Please run `playwright install chromium` and retry."
            ),
            "results": [],
        }
    logger.info("[job_search] raw_jobs count=%d", len(raw_jobs))

    if not raw_jobs:
        return {
            "tool": "company_job_search",
            "company": args.company,
            "careers_url": careers_url,
            "message": "No job listings found on the careers page.",
            "results": [],
        }

    # Step 3: Rank by resume fit
    ranked = await _rank_by_resume(raw_jobs, user_id, args.role, args.max_results)
    logger.info("[job_search] ranked jobs count=%d", len(ranked))

    location_label = ", ".join(filter(None, [args.city, country]))
    return {
        "tool": "company_job_search",
        "company": args.company,
        "location": location_label,
        "include_remote": args.include_remote,
        "careers_url": careers_url,
        "total_found": len(raw_jobs),
        "results": ranked,
    }


# ── Resume-based ranking ────────────────────────────────────────────────────────

_RANKER_SYSTEM = """You are a career fit scorer. Given a candidate's profile and a list
of job listings, score each job 0-10 for how well the candidate fits and give a
one-sentence reason. A 10 means the role is an almost perfect match for the
candidate's skills and experience level.

Return JSON exactly:
{
  "ranked": [
    {"index": 0, "fit_score": 8, "reason": "..."},
    ...
  ]
}

Sort by fit_score descending. Include all jobs."""


async def _rank_by_resume(
    jobs: list[dict[str, Any]],
    user_id: str,
    target_role: Optional[str],
    top_k: int,
) -> list[dict[str, Any]]:
    """Score each job against the user's resume and return the top-k."""
    from agent.llm import complete_json

    # Pull career facts from ChromaDB memory
    query = target_role or "software engineer"
    facts_docs, _, _ = chroma.query(chroma.memory_ns(user_id), query, n_results=8)

    # Pull canonical profile summary from SQLite
    profile_summary = _build_profile_summary(user_id)

    job_list = [
        {
            "index": i,
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "snippet": j.get("snippet", "")[:300],
        }
        for i, j in enumerate(jobs)
    ]

    user_prompt = (
        f"Candidate profile:\n{profile_summary}\n\n"
        f"Career facts:\n" + "\n".join(f"- {f}" for f in facts_docs) + "\n\n"
        f"Target role: {target_role or 'not specified'}\n\n"
        f"Jobs to rank:\n{json.dumps(job_list, indent=2)}"
    )

    result = await complete_json(_RANKER_SYSTEM, user_prompt[:14000])
    ranked_meta = result.get("ranked", []) if isinstance(result, dict) else []

    output: list[dict[str, Any]] = []
    for item in ranked_meta[:top_k]:
        idx = item.get("index", -1)
        if 0 <= idx < len(jobs):
            job = dict(jobs[idx])
            job["fit_score"] = item.get("fit_score", 0)
            job["fit_reason"] = item.get("reason", "")
            output.append(job)

    # Ensure sorted descending by fit_score
    output.sort(key=lambda j: j.get("fit_score", 0), reverse=True)
    return output


def _build_profile_summary(user_id: str) -> str:
    """Build a compact candidate summary from the SQLite resume profile."""
    profile = sqlite.get_current_resume(user_id)
    if not profile:
        return "No resume on file."
    data = profile.get("data", {})
    return json.dumps(
        {
            "summary": data.get("summary", ""),
            "skills": data.get("skills", [])[:20],
            "experience": [
                {
                    "title": e.get("title", ""),
                    "company": e.get("company", ""),
                    "start": e.get("start", ""),
                    "end": e.get("end", ""),
                }
                for e in data.get("experience", [])[:4]
            ],
            "education": [
                {"degree": e.get("degree", ""), "institution": e.get("institution", "")}
                for e in data.get("education", [])[:2]
            ],
        },
        indent=2,
    )
