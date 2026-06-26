"""Company research tool.

Fetches a company's public web presence, scrapes the text with BeautifulSoup,
and uses GPT-4o to summarise mission, products, recent news, culture and
interview-prep talking points.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from agent.llm import complete_json

DESCRIPTION = (
    "Research a company (mission, products, recent news, culture) to help the "
    "user prepare for applications and interviews."
)


class ToolSchema(BaseModel):
    company: str = Field(..., description="Company name.")
    url: Optional[str] = Field(None, description="Company website or careers URL.")


_RESEARCH_SYSTEM = """You are a company-research analyst. Given scraped text and a \
company name, produce a JSON object:
{
  "overview": "",
  "products": ["..."],
  "recent_news": ["..."],
  "culture": ["..."],
  "interview_talking_points": ["..."]
}
Only use information supported by the provided text; leave arrays empty if unknown."""


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)
    scraped = ""
    if args.url:
        scraped = await _scrape(args.url)

    summary = await complete_json(
        _RESEARCH_SYSTEM,
        f"COMPANY: {args.company}\n\nSCRAPED TEXT:\n{scraped[:14000]}",
    )
    return {
        "tool": "company_research",
        "company": args.company,
        "url": args.url,
        "research": summary,
    }


async def _scrape(url: str) -> str:
    from bs4 import BeautifulSoup

    if "://" not in url:
        url = f"https://{url}"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20.0, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())
