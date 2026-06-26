"""Company job-search tool.

Discovers open roles at a target company (optionally filtered by location) via
DuckDuckGo, then ranks results by ATS-domain trust and query relevance.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.playwright import ats_detector

DESCRIPTION = (
    "Search the web for open job postings at a given company and location, "
    "returning ranked links with detected ATS platform."
)


class ToolSchema(BaseModel):
    company: str = Field(..., description="Company name, e.g. 'Stripe'.")
    location: Optional[str] = Field(None, description="City / region filter.")
    role: Optional[str] = Field(None, description="Target role / title keyword.")
    max_results: int = Field(8, ge=1, le=25)


# Domains that strongly indicate a real application page.
_ATS_DOMAINS = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "workday")


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)
    query = _build_query(args)
    raw = await _search(query, args.max_results)

    ranked = []
    for item in raw:
        url = item.get("href") or item.get("url", "")
        title = item.get("title", "")
        snippet = item.get("body", "") or item.get("snippet", "")
        platform = ats_detector.detect_from_url(url) or "generic"
        ranked.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "ats_platform": platform,
                "score": _score(url, title, args),
            }
        )

    ranked.sort(key=lambda r: r["score"], reverse=True)
    return {
        "tool": "company_job_search",
        "company": args.company,
        "location": args.location,
        "query": query,
        "results": ranked[: args.max_results],
    }


def _build_query(args: ToolSchema) -> str:
    parts = [args.company, "careers jobs"]
    if args.role:
        parts.append(args.role)
    if args.location:
        parts.append(args.location)
    parts.append("(greenhouse OR lever OR workday)")
    return " ".join(parts)


async def _search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Run a DuckDuckGo text search. Returns [] if the dependency is missing."""
    try:
        from duckduckgo_search import DDGS
    except Exception:
        return []
    import asyncio

    def _blocking() -> list[dict[str, Any]]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results * 2))

    try:
        return await asyncio.to_thread(_blocking)
    except Exception:
        return []


def _score(url: str, title: str, args: ToolSchema) -> float:
    score = 0.0
    low_url = (url or "").lower()
    low_title = (title or "").lower()
    if any(d in low_url for d in _ATS_DOMAINS):
        score += 5.0
    if args.company.lower() in low_url or args.company.lower() in low_title:
        score += 2.0
    if args.role and args.role.lower() in low_title:
        score += 2.0
    if args.location and args.location.lower() in low_title:
        score += 1.0
    if re.search(r"/jobs?/|/careers?/|/postings?/", low_url):
        score += 1.0
    return score
