"""Broad-coverage careers page scraper using Crawl4AI.

This version is designed to be much closer to platform-agnostic coverage than a
single-ATS scraper:

1. Resolve careers URL from catalog/search.
2. Crawl multiple likely search URLs.
3. Extract jobs from:
   - JSON-LD JobPosting
   - embedded JSON / app state
   - HTML anchors
   - markdown links
   - raw URLs in scripts/text
4. Follow limited pagination / next-page links.
5. Use GPT only as a last resort.

It is still not possible to guarantee 100% coverage for every careers site on the
internet. Sites that require auth, block bots, or expose jobs only via dynamic
network APIs may still need special handling."""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

logger = logging.getLogger(__name__)
CATALOGUE_PATH = Path(__file__).parent.parent / "tools" / "company_careers.json"

# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def _load_catalogue() -> dict[str, str]:
    try:
        raw = json.loads(CATALOGUE_PATH.read_text())
        if isinstance(raw, dict):
            out: dict[str, str] = {}
            for k, v in raw.items():
                if isinstance(v, dict) and isinstance(v.get("careers_url"), str):
                    out[k] = v["careers_url"]
            return out
    except Exception as exc:
        logger.warning("[careers] catalogue load failed: %s", exc)
    return {}


COMPANY_CATALOGUE = _load_catalogue()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_unicode_escapes(text: str) -> str:
    text = html_lib.unescape(text)
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _normalize_url(url: str, base_url: str | None = None) -> str:
    url = html_lib.unescape(url.strip())
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    qs = parse_qs(parsed.query, keep_blank_values=True)
    kept: list[tuple[str, str]] = []
    for k, values in qs.items():
        if k.lower().startswith("utm_") or k.lower() in {"fbclid", "gclid", "ref", "source"}:
            continue
        for v in values:
            kept.append((k, v))
    return urlunparse(parsed._replace(query=urlencode(kept, doseq=True)))


def _job_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    slug = parts[-1]
    if len(parts) >= 2 and parts[-2].lower() == "job":
        slug = parts[-1]
    slug = re.sub(r"(_R\d+|[-_]?job\d+|[-_]?req\d+)$", "", slug, flags=re.I)
    slug = slug.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", slug).strip().title()


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("url") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _jobish(url: str) -> bool:
    return bool(
        re.search(
            r"/job/|/jobs/|/position/|/opening/|/requisition/|/posting/|/careers/|/vacancies/|"
            r"jobId=|job_id=|req_id=|reqId=|referenceid=|gh_jid=|lever=|ashby=|workday=|jobdetails|"
            r"api/jobs|search/jobs|careers/search",
            url,
            flags=re.I,
        )
    )


# ---------------------------------------------------------------------------
# Careers URL lookup
# ---------------------------------------------------------------------------


async def find_careers_url(company: str) -> Optional[str]:
    slug = _slug(company)
    for key, url in COMPANY_CATALOGUE.items():
        if _slug(key) == slug:
            return url
    for key, url in COMPANY_CATALOGUE.items():
        k = _slug(key)
        if k and (k in slug or slug in k):
            return url
    return await _ddg_find_careers(company)


# ---------------------------------------------------------------------------
# ATS detection and search URL generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ATSProfile:
    name: str
    templates: tuple[str, ...]
    query_keys: tuple[str, ...]


ATS_PROFILES: tuple[ATSProfile, ...] = (
    ATSProfile("phenom", ("{base}{locale}/search-results?{params}", "{base}/search-results?{params}"), ("keywords", "q", "search")),
    ATSProfile("workday", ("{base}/search?{params}", "{base}/jobs?{params}", "{base}/jobs"), ("title", "jobTitle", "keywords", "q")),
    ATSProfile("greenhouse", ("{base}/jobs?{params}", "{base}/jobs", "{base}/job?{params}"), ("keywords", "query", "q")),
    ATSProfile("lever", ("{base}/jobs?{params}", "{base}/jobs"), ("search", "q", "keywords")),
    ATSProfile("ashby", ("{base}/jobs?{params}", "{base}/jobs"), ("jobTitle", "title", "keywords", "q")),
    ATSProfile("smartrecruiters", ("{base}/jobs?{params}", "{base}/jobs"), ("searchKey", "keywords", "q")),
    ATSProfile("icims", ("{base}/jobs/search?{params}", "{base}/careers/search?{params}", "{base}/jobs/search"), ("search", "keywords", "q")),
    ATSProfile("taleo", ("{base}/careers/search?{params}", "{base}/search?{params}", "{base}/careers/search"), ("q", "keywords", "search")),
    ATSProfile("generic", ("{base}/search-results?{params}", "{base}/search?{params}", "{base}/jobs/search?{params}", "{base}/careers/search?{params}", "{base}?{params}"), ("keywords", "q", "search")),
)


def detect_ats(careers_url: str, html: str = "", markdown: str = "") -> str:
    blob = f"{careers_url}\n{html[:30000]}\n{markdown[:30000]}".lower()
    checks = {
        "phenom": ["phenom", "jobsearch", "search-results"],
        "workday": ["workday", "wd5", "myworkdayjobs", "jobdetails"],
        "greenhouse": ["greenhouse"],
        "lever": ["lever.co", "lever"],
        "ashby": ["ashby", "ashbyhq"],
        "smartrecruiters": ["smartrecruiters"],
        "icims": ["icims"],
        "taleo": ["taleo"],
    }
    for name, needles in checks.items():
        if any(n in blob for n in needles):
            return name
    return "generic"


def _search_urls(careers_url: str, role: str, location: str, ats: str) -> list[str]:
    parsed = urlparse(careers_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else careers_url.rstrip("/")
    base = base.rstrip("/")

    locale = ""
    m = re.search(r"(/[a-z]{2}/en)(?:/|$)", careers_url)
    if m:
        locale = m.group(1)

    def params_for(profile: ATSProfile) -> str:
        pairs: list[tuple[str, str]] = []
        for key in profile.query_keys:
            if key in {"keywords", "query", "q", "search", "title", "jobTitle", "searchKey"}:
                pairs.append((key, role))
            elif key in {"location", "city", "country", "loc"} and location:
                pairs.append((key, location))
        if not pairs:
            pairs = [("keywords", role)]
            if location:
                pairs.append(("location", location))
        return urlencode(pairs, doseq=True, quote_via=quote)

    ordered = [p for p in ATS_PROFILES if p.name == ats] + [p for p in ATS_PROFILES if p.name != ats]
    out: list[str] = []
    for profile in ordered:
        params = params_for(profile)
        for tmpl in profile.templates:
            url = tmpl.format(base=base, locale=locale, params=params)
            if url not in out:
                out.append(url)
    if careers_url not in out:
        out.append(careers_url)
    return out


# ---------------------------------------------------------------------------
# Public scrape API
# ---------------------------------------------------------------------------


async def scrape_jobs(
    company: str,
    careers_url: str,
    role: str,
    country: str,
    city: Optional[str] = None,
    max_jobs: int = 20,
) -> list[dict[str, Any]]:
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
    except Exception as exc:
        logger.error("[scrape_jobs] crawl4ai unavailable: %s", exc)
        return []

    location = ", ".join([x for x in [city, country] if x])
    ats = detect_ats(careers_url)
    urls = _search_urls(careers_url, role=role, location=location, ats=ats)

    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(
        delay_before_return_html=2.0,
        remove_overlay_elements=True,
        word_count_threshold=0,
        page_timeout=45000,
    )

    logger.info("[scrape_jobs] company=%s ats=%s urls=%d", company, ats, len(urls))

    for idx, url in enumerate(urls[:8], start=1):
        html = ""
        markdown = ""
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
                inner = result._results[0] if hasattr(result, "_results") and result._results else result
                html = getattr(inner, "html", "") or ""
                md = getattr(inner, "markdown", "")
                markdown = md if isinstance(md, str) else str(md or "")
        except Exception as exc:
            logger.warning("[scrape_jobs] crawl failed url=%s err=%s", url, exc)
            continue

        logger.info("[scrape_jobs] try=%d html=%d md=%d url=%s", idx, len(html), len(markdown), url)
        if not html and not markdown:
            continue

        jobs = _extract_from_jsonld(html, company, max_jobs)
        if jobs:
            return validate_jobs(_dedupe(jobs), company, max_jobs)

        jobs = _extract_from_embedded_json(html, company, max_jobs)
        if jobs:
            jobs = _maybe_follow_pagination(jobs, html=html, markdown=markdown, base_url=url, company=company, max_jobs=max_jobs)
            return validate_jobs(_dedupe(jobs), company, max_jobs)

        jobs = _extract_from_html(html, url, company, max_jobs)
        if jobs:
            jobs = _maybe_follow_pagination(jobs, html=html, markdown=markdown, base_url=url, company=company, max_jobs=max_jobs)
            return validate_jobs(_dedupe(jobs), company, max_jobs)

        jobs = _extract_from_markdown(markdown, company, max_jobs)
        if jobs:
            jobs = _maybe_follow_pagination(jobs, html=html, markdown=markdown, base_url=url, company=company, max_jobs=max_jobs)
            return validate_jobs(_dedupe(jobs), company, max_jobs)

        jobs = _extract_from_text(html + "\n" + markdown, url, company, max_jobs)
        if jobs:
            return validate_jobs(_dedupe(jobs), company, max_jobs)

    # Last resort only.
    content = markdown if markdown else html[:30000]
    jobs = await _gpt_extract(content, company, role, location, max_jobs)
    return validate_jobs(_dedupe(jobs), company, max_jobs)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


_ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_MD_RE = re.compile(r"\[([^\]]{2,200})\]\((https?://[^\)]+)\)")
_NEXT_RE = re.compile(r'<a\b[^>]*(?:rel=["\']next["\']|aria-label=["\'][^"\']*next[^"\']*["\'])[^>]*href=["\']([^"\']+)["\']', re.I)


def _extract_from_jsonld(html: str, company: str, max_jobs: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S)
    for block in blocks:
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                items.append(data)
            if isinstance(data.get("@graph"), list):
                items.extend([x for x in data["@graph"] if isinstance(x, dict) and x.get("@type") == "JobPosting"])
        elif isinstance(data, list):
            items.extend([x for x in data if isinstance(x, dict) and x.get("@type") == "JobPosting"])

        for item in items:
            url = str(item.get("url") or item.get("sameAs") or "").strip()
            if not url:
                continue
            title = str(item.get("title") or "").strip() or _job_title_from_url(url)
            jobs.append({
                "company": company,
                "ats_platform": "jsonld",
                "title": title,
                "url": url,
                "location": _jsonld_location(item),
                "snippet": _clean_text(str(item.get("description", "")))[:250],
            })
            if len(jobs) >= max_jobs:
                return jobs
    return jobs


def _jsonld_location(item: dict[str, Any]) -> str:
    loc = item.get("jobLocation")
    if isinstance(loc, dict):
        addr = loc.get("address", {})
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            return ", ".join([str(x) for x in parts if x])
    if isinstance(loc, list) and loc and isinstance(loc[0], dict):
        return _jsonld_location({"jobLocation": loc[0]})
    return ""


def _extract_from_embedded_json(html: str, company: str, max_jobs: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    blobs = [s for s in scripts if s.strip() and any(k in s.lower() for k in ("__next_data__", "initialstate", "job", "requisition", "opening", "vacanc", "career", "applyurl", "jobposting"))]
    blobs.append(html[:200000])

    for blob in blobs:
        # explicit URL keys
        for m in re.finditer(r'"(?:applyUrl|url|apply_url|jobUrl|job_url|link|href)"\s*:\s*"(https?://[^"\\]+)"', blob, flags=re.I):
            url = _normalize_url(m.group(1))
            if not _jobish(url):
                continue
            ctx = blob[m.end(): m.end() + 3000]
            title_m = re.search(r'"(?:title|jobTitle|positionTitle|name)"\s*:\s*"([^"\\]{2,180})"', ctx, flags=re.I)
            loc_m = re.search(r'"(?:location|cityState|city|state|country|address)"\s*:\s*"([^"\\]{2,180})"', ctx, flags=re.I)
            desc_m = re.search(r'"(?:description|descriptionTeaser|summary|snippet)"\s*:\s*"([^"\\]{10,500})"', ctx, flags=re.I)
            jobs.append({
                "company": company,
                "ats_platform": "embedded_json",
                "title": _decode_unicode_escapes(title_m.group(1)) if title_m else _job_title_from_url(url),
                "url": url,
                "location": _decode_unicode_escapes(loc_m.group(1)) if loc_m else "",
                "snippet": _decode_unicode_escapes(desc_m.group(1))[:250] if desc_m else "",
            })
            if len(jobs) >= max_jobs:
                return jobs

        # JSON arrays stored as strings
        for m in re.finditer(r'"jobs?"\s*:\s*(\[.*?\])', blob, flags=re.I | re.S):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or item.get("applyUrl") or item.get("jobUrl") or "").strip()
                if not url:
                    continue
                url = _normalize_url(url)
                jobs.append({
                    "company": company,
                    "ats_platform": "embedded_json",
                    "title": str(item.get("title") or item.get("jobTitle") or item.get("name") or "").strip() or _job_title_from_url(url),
                    "url": url,
                    "location": str(item.get("location") or item.get("cityState") or "").strip(),
                    "snippet": str(item.get("description") or item.get("summary") or "").strip()[:250],
                })
                if len(jobs) >= max_jobs:
                    return jobs

    return jobs


def _extract_from_html(html: str, base_url: str, company: str, max_jobs: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for href, inner in _ANCHOR_RE.findall(html):
        url = _normalize_url(href, base_url)
        if not _jobish(url):
            continue
        title = _clean_text(inner) or _job_title_from_url(url)
        if not title:
            continue
        jobs.append({
            "company": company,
            "ats_platform": "html_anchor",
            "title": title,
            "url": url,
            "location": "",
            "snippet": "",
        })
        if len(jobs) >= max_jobs:
            break
    return jobs


def _extract_from_markdown(markdown: str, company: str, max_jobs: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _MD_RE.finditer(markdown or ""):
        title, url = m.group(1).strip(), _normalize_url(m.group(2).strip())
        if not _jobish(url) or url in seen:
            continue
        seen.add(url)
        jobs.append({
            "company": company,
            "ats_platform": "markdown_link",
            "title": title or _job_title_from_url(url),
            "url": url,
            "location": "",
            "snippet": "",
        })
        if len(jobs) >= max_jobs:
            break
    return jobs


def _extract_from_text(text: str, base_url: str, company: str, max_jobs: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r"https?://[^\s\)\]\">']+", text):
        url = _normalize_url(m.group(0), base_url)
        if url in seen or not _jobish(url):
            continue
        seen.add(url)
        jobs.append({
            "company": company,
            "ats_platform": "text_blob",
            "title": _job_title_from_url(url),
            "url": url,
            "location": "",
            "snippet": "",
        })
        if len(jobs) >= max_jobs:
            break
    return jobs


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def _maybe_follow_pagination(
    jobs: list[dict[str, Any]],
    *,
    html: str,
    markdown: str,
    base_url: str,
    company: str,
    max_jobs: int,
) -> list[dict[str, Any]]:
    if len(jobs) >= max_jobs:
        return jobs[:max_jobs]

    candidates: list[str] = []
    for m in _NEXT_RE.finditer(html):
        href = m.group(1)
        if href:
            candidates.append(_normalize_url(href, base_url))

    # also search for page/offset/start query URLs in visible text
    for text in (html, markdown):
        for m in re.finditer(r"https?://[^\s\)\]\">']*(?:page=\d+|offset=\d+|start=\d+)[^\s\)\]\">']*", text, flags=re.I):
            candidates.append(_normalize_url(m.group(0), base_url))

    candidates = list(dict.fromkeys(candidates))[:2]
    if not candidates:
        return jobs

    async def _crawl_one(url: str) -> tuple[str, str]:
        try:
            from crawl4ai import AsyncWebCrawler
            from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig

            browser_cfg = BrowserConfig(headless=True, verbose=False)
            run_cfg = CrawlerRunConfig(delay_before_return_html=1.5, remove_overlay_elements=True, word_count_threshold=0, page_timeout=30000)
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
                inner = result._results[0] if hasattr(result, "_results") and result._results else result
                h = getattr(inner, "html", "") or ""
                md = getattr(inner, "markdown", "")
                return h, md if isinstance(md, str) else str(md or "")
        except Exception:
            return "", ""

    async def _run() -> list[tuple[str, str]]:
        return await asyncio.gather(*[_crawl_one(u) for u in candidates])

    try:
        pages = asyncio.run(_run())
    except RuntimeError:
        # Already inside an event loop — skip pagination rather than fail.
        return jobs
    except Exception:
        return jobs

    extra: list[dict[str, Any]] = []
    for (h, md), url in zip(pages, candidates):
        extra.extend(_extract_from_jsonld(h, company, max_jobs))
        extra.extend(_extract_from_embedded_json(h, company, max_jobs))
        extra.extend(_extract_from_html(h, url, company, max_jobs))
        extra.extend(_extract_from_markdown(md, company, max_jobs))
        if len(jobs) + len(extra) >= max_jobs:
            break

    return _dedupe(jobs + extra)[:max_jobs]


# ---------------------------------------------------------------------------
# GPT fallback
# ---------------------------------------------------------------------------


async def _gpt_extract(content: str, company: str, role: str, location: str, max_jobs: int) -> list[dict[str, Any]]:
    from agent.llm import complete_json

    system = (
        "You are a strict job extraction engine. Return only JSON with shape "
        '{"jobs": [{"title": "", "url": "", "location": "", "snippet": ""}]}. '
        f"Focus on {company} roles matching '{role}' in {location} or remote. "
        f"Return at most {max_jobs} jobs. If none, return {{\"jobs\": []}}."
    )

    result = await complete_json(system, content[:20000])
    if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
        return []

    out: list[dict[str, Any]] = []
    for item in result["jobs"][:max_jobs]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not title and not url:
            continue
        out.append({
            "company": company,
            "ats_platform": "gpt4o",
            "title": title or _job_title_from_url(url),
            "url": url,
            "location": str(item.get("location", "")).strip(),
            "snippet": str(item.get("snippet", "")).strip()[:250],
        })
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_ACTION_SUFFIX_RE = re.compile(r"/(apply|submit|apply-now|apply_now)/?$", re.I)


def validate_jobs(jobs: list[dict[str, Any]], company: str, max_jobs: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title or not url:
            continue
        url = _normalize_url(url)
        # Strip action suffixes so we link to the job page, not the apply form
        url = _ACTION_SUFFIX_RE.sub("", url)
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        item["company"] = company
        item["title"] = title
        item["url"] = url
        item.setdefault("ats_platform", "unknown")
        item.setdefault("location", "")
        item.setdefault("snippet", "")
        out.append(item)
        if len(out) >= max_jobs:
            break
    return out


# ---------------------------------------------------------------------------
# URL fallback lookup
# ---------------------------------------------------------------------------


async def _ddg_find_careers(company: str) -> Optional[str]:
    try:
        from duckduckgo_search import DDGS
    except Exception:
        return None

    company_slug = _slug(company)
    queries = [
        f"{company} official careers jobs page",
        f"site:greenhouse.io OR site:lever.co OR site:workday.com OR site:ashbyhq.com OR site:smartrecruiters.com {company} jobs",
    ]

    for query in queries:
        try:
            def _search() -> list[dict[str, Any]]:
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=8))

            results = await asyncio.to_thread(_search)
        except Exception:
            continue

        for r in results:
            url = str(r.get("href") or r.get("url") or "").strip()
            if not url:
                continue
            if any(k in url.lower() for k in ("careers", "jobs", "join-us", "joinus", "workwithus")) and company_slug in _slug(url):
                return url
        for r in results:
            url = str(r.get("href") or r.get("url") or "").strip()
            if url:
                return url
    return None


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------


def explain_pipeline() -> str:
    return (
        "1) resolve careers URL\n"
        "2) detect ATS and generate multiple candidate search URLs\n"
        "3) crawl pages and extract jobs from JSON-LD, embedded JSON, HTML anchors, markdown, and raw URLs\n"
        "4) optionally follow limited pagination\n"
        "5) validate and dedupe\n"
        "6) use GPT only as a last resort"
    )
