"""ATS platform detection.

Detection is purely URL-pattern + DOM-based — no LLM calls. Returns one of
``greenhouse``, ``lever``, ``workday`` or ``generic``.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

# URL host/path signatures.
_URL_SIGNATURES = {
    "greenhouse": ["greenhouse.io", "boards.greenhouse", "grnh.se"],
    "lever": ["lever.co", "jobs.lever"],
    "workday": ["myworkdayjobs.com", "workday.com", "wd1.", "wd5."],
}

# DOM signatures (substrings expected in page HTML / specific selectors).
_DOM_SIGNATURES = {
    "greenhouse": ["id=\"application_form\"", "greenhouse", "input[name='first_name']"],
    "lever": ["lever-application", "urls[LinkedIn]", "postings.lever"],
    "workday": ["data-automation-id", "wd-popup", "workday"],
}


def detect_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    haystack = f"{parsed.netloc}{parsed.path}".lower()
    for platform, sigs in _URL_SIGNATURES.items():
        if any(sig in haystack for sig in sigs):
            return platform
    return None


def detect_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    lowered = html.lower()
    for platform, sigs in _DOM_SIGNATURES.items():
        if any(sig.lower() in lowered for sig in sigs):
            return platform
    return None


def detect(url: str = "", html: str = "") -> str:
    """Combine URL and DOM signals; default to ``generic``."""
    return detect_from_url(url) or detect_from_html(html) or "generic"


async def detect_on_page(page, url: str = "") -> str:
    """Detect using a live Playwright ``page`` object."""
    platform = detect_from_url(url or page.url)
    if platform:
        return platform
    try:
        html = await page.content()
    except Exception:
        html = ""
    return detect_from_html(html) or "generic"
