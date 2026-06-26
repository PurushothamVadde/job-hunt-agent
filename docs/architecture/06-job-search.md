# Company Job Search Pipeline

Triggered when the user says something like "Find me jobs at Stripe in New York" or "Search Adobe for remote ML roles". The agent parses company and location from natural language, discovers the careers page, scrapes job listings, ranks them by resume match, and streams a markdown table back to the user.

## Flow Diagram

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ccfbf1', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#f0fdfa'}}}%%
flowchart TD
    START(["User: 'Find jobs at {company} in {location}'"])
    PARSE["Agent parses company + location\nfrom natural language"]

    subgraph Step1["Step 1 — Careers Page Discovery"]
        DDG1["DuckDuckGo: '{company} careers jobs site:{domain}'"]
        FB["Fallback: '{company} jobs {location}'"]
        URL["Careers page URL resolved"]
        DDG1 -- found --> URL
        DDG1 -- not found --> FB --> URL
    end

    subgraph Step2["Step 2 — Job Listing Scrape"]
        FETCH["httpx GET careers page"]
        SCRAPE["BeautifulSoup extract:\ntitle · location · URL · snippet"]
        JS_FB["JS-rendered page?\nFallback: DuckDuckGo site-scoped search\n'site:{careers_url} {location} software engineer'"]
        FETCH --> SCRAPE
        FETCH -- JS wall detected --> JS_FB --> SCRAPE
    end

    subgraph Step3["Step 3 — Resume-Match Ranking"]
        EMBED["sentence-transformers embed each job snippet"]
        COS["Cosine similarity vs stored resume embedding\n(ChromaDB namespace: resume:{user_id})"]
        RANK["Sort descending — return top 10"]
    end

    subgraph Step4["Step 4 — Results Streamed to User"]
        TABLE["Markdown table:\nRank | Title | Location | Match% | Apply Link"]
        NEXT["User: 'tailor my resume for #3'\n→ invoke Resume Tailor Tool"]
    end

    START --> PARSE --> DDG1
    URL --> FETCH
    SCRAPE --> EMBED --> COS --> RANK --> TABLE --> NEXT
```

## Input / Output Schema

### Tool Input

```python
{
    "company_name": str,   # e.g. "Stripe"
    "location": str,       # e.g. "New York" or "remote"
    "max_results": int     # default 10
}
```

### Tool Output

```python
[
    {
        "rank": 1,
        "title": "Senior Software Engineer, Payments",
        "url": "https://stripe.com/jobs/listing/...",
        "location": "New York, NY",
        "snippet": "Work on Stripe's core payments infrastructure...",
        "match_score": 0.87
    },
    ...
]
```

## Ranking Algorithm

| Step | Detail |
|------|--------|
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` (local, free) |
| Resume vector | Retrieved from ChromaDB namespace `resume:{user_id}` |
| Job snippet vector | Embedded on the fly per listing |
| Similarity metric | Cosine similarity |
| Output | Top 10 sorted by score descending |

## Careers Page Discovery Strategy

1. Try DuckDuckGo: `{company} careers jobs site:{company_domain}`
2. If no result: fall back to `{company} jobs {location}`
3. If careers page is JS-rendered (httpx returns no job listings): fall back to DuckDuckGo site-scoped search: `site:{careers_url} {location} software engineer`

## Result Table Format

The agent streams this as a markdown table in the chat:

```
| Rank | Title                              | Location    | Match % | Apply |
|------|------------------------------------|-------------|---------|-------|
| 1    | Senior Software Engineer, Payments | New York    | 87%     | [Link](url) |
| 2    | Backend Engineer, Risk             | Remote      | 81%     | [Link](url) |
```

After seeing the table, the user can say:
- `"tailor my resume for #1"` → invokes Resume Tailor Tool
- `"apply to #2"` → invokes Auto-Apply Tool
- `"tell me more about Stripe"` → invokes Company Research Tool

## Implementation Files

| File | Responsibility |
|------|---------------|
| `agent/tools/company_job_search.py` | Full pipeline: discovery → scrape → rank → return |
| `db/chroma.py` | Resume embedding retrieval for cosine comparison |
