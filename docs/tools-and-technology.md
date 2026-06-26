# Tools & Technology

Complete reference for every library, service, and tool used in JobHuntAI. Organised by layer — click any section to jump to it.

---

## Table of Contents

1. [Core Runtime](#1-core-runtime)
2. [Agent Orchestration — LangGraph](#2-agent-orchestration--langgraph)
3. [LLM — OpenAI GPT-4o](#3-llm--openai-gpt-4o)
4. [Persistent Memory](#4-persistent-memory)
5. [Embeddings](#5-embeddings)
6. [Streaming — SSE](#6-streaming--sse)
7. [Web Scraping & Job Search](#7-web-scraping--job-search)
8. [Resume Processing](#8-resume-processing)
9. [Browser Automation — Playwright](#9-browser-automation--playwright)
10. [Frontend](#10-frontend)
11. [Observability — LangSmith](#11-observability--langsmith)
12. [MCP Integration](#12-mcp-integration)
13. [Configuration & Secrets](#13-configuration--secrets)
14. [Full Dependency List](#14-full-dependency-list)

---

## 1. Core Runtime

| Item | Detail |
|------|--------|
| **Language** | Python 3.11+ |
| **Web framework** | FastAPI |
| **ASGI server** | Uvicorn |
| **Async runtime** | `asyncio` (built-in) |

### FastAPI

**Package:** `fastapi`, `uvicorn[standard]`

Used as the HTTP/SSE gateway. Every frontend action — chat messages, resume uploads, HITL decisions, feedback votes — enters the system through a FastAPI endpoint. FastAPI's async request handling pairs naturally with LangGraph's `astream()` for non-blocking SSE.

Key endpoints:

| Endpoint | Handler file |
|----------|-------------|
| `POST /chat/stream` | `api/chat.py` |
| `POST /chat/approve` | `api/chat.py` |
| `POST /resume/upload` | `api/resume.py` |
| `GET /resume/versions` | `api/resume.py` |
| `POST /documents/upload` | `api/documents.py` |
| `GET /applications` | `api/applications.py` |
| `POST /feedback` | `observability/langsmith.py` |
| `GET /admin/traces` | `observability/langsmith.py` |

---

## 2. Agent Orchestration — LangGraph

**Package:** `langgraph`, `langchain-core`

LangGraph is the core of the system. It provides the `StateGraph` that runs on every user message, managing the ReAct loop, tool dispatch, HITL interrupts, and post-turn memory writes.

### StateGraph Nodes

```
plan → tool_select → tool_execute → synthesize → respond
```

| Node | What it does |
|------|-------------|
| `plan` | GPT-4o breaks the query into ordered sub-goals |
| `tool_select` | GPT-4o picks the next tool via ReAct reasoning |
| `tool_execute` | Runs the selected tool; appends result to state |
| `synthesize` | Merges all tool outputs into a coherent intermediate answer |
| `respond` | Streams final answer token-by-token over SSE |
| `interrupt()` | Suspends graph execution at HITL gates; resumes on `POST /chat/approve` |

### Graph State Shape

```python
class AgentState(TypedDict):
    session_id:    str
    user_id:       str
    messages:      list[BaseMessage]
    memories:      list[str]
    tool_results:  list[ToolResult]
    pending_goals: list[str]
    hitl_pending:  bool
    hitl_decision: str | None
```

### Why LangGraph?

- First-class `interrupt()` support for HITL — pauses the graph mid-run and resumes exactly where it left off
- `astream()` with `stream_mode="events"` yields every node transition and token in real time
- Automatic LangSmith tracing of every node, edge, and tool call with zero manual instrumentation

---

## 3. LLM — OpenAI GPT-4o

**Package:** `openai`
**Required env var:** `OPENAI_API_KEY`
**Model ID:** `gpt-4o`

GPT-4o drives every intelligent step in the system:

| Task | Where |
|------|-------|
| ReAct reasoning (plan, tool_select, synthesize) | `agent/graph.py` |
| Structured resume extraction (contact, skills, experience…) | `agent/resume/ingestion.py` |
| Gap analysis + bullet rewrite | `agent/resume/tailoring.py` |
| Semantic fact extraction ("has 5 yrs Python") | `agent/memory.py` |
| Session auto-summarize | `agent/memory.py` |

### Streaming

The OpenAI SDK's streaming API is used for the `respond` node. Tokens are forwarded to the SSE generator in `api/chat.py` so the browser renders words as they arrive.

```python
stream = await client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    stream=True
)
async for chunk in stream:
    token = chunk.choices[0].delta.content
    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
```

---

## 4. Persistent Memory

The system uses two storage tiers — one relational, one vector — that play complementary roles.

### SQLite

**Package:** `sqlite3` (Python built-in)
**Implementation:** `db/sqlite.py`

Stores all structured, relational data. Acts as the system of record for everything that *happened*.

| Table | Contents |
|-------|----------|
| `users` | Tenant table — all other tables FK to `user_id` |
| `sessions` | One row per conversation; `title`, `last_active` |
| `messages` | Full message history per session (role + content) |
| `resume_profiles` | Version-controlled resume uploads; `data_json`, `master_pdf_path` |
| `episodic_memories` | Auto-generated summaries written at session end |
| `applications` | Job tracker: company, role, URL, status, dates, notes |

**Why SQLite?** Zero-config, file-based, ships with Python. Perfectly adequate for single-user or small-team workloads. Swap for PostgreSQL for multi-tenant production.

### ChromaDB

**Package:** `chromadb`
**Implementation:** `db/chroma.py`

Stores vector embeddings for semantic search. Acts as the system of record for everything that is *true about the user*.

| Namespace | Content | Populated by |
|-----------|---------|--------------|
| `memory:{user_id}` | Career facts extracted by GPT-4o | Session end + resume ingestion |
| `resume:{user_id}` | Chunked + embedded master resume | Resume ingestion pipeline |
| `docs:{user_id}` | Uploaded career documents | `POST /documents/upload` |

Chunking strategy: **512 tokens per chunk, 50-token overlap** — balances context richness with retrieval precision.

---

## 5. Embeddings

**Package:** `sentence-transformers`
**Model:** `sentence-transformers/all-MiniLM-L6-v2`
**Runs:** locally — no API key, no cost

Used everywhere a vector similarity score is needed:

- Indexing resume chunks into ChromaDB (`resume:{user_id}`)
- Indexing career documents (`docs:{user_id}`)
- Storing semantic memory facts (`memory:{user_id}`)
- Ranking job postings by cosine similarity to the user's resume embedding (job search tool)

The same model is used for both indexing and querying so embedding space is consistent.

---

## 6. Streaming — SSE

**Package:** `sse-starlette`
**Frontend lib:** `@microsoft/fetch-event-source` (for POST-based SSE)

The backend pushes a mix of event types over a single long-lived SSE connection:

| Event type | When |
|------------|------|
| `token` | Each streamed GPT-4o output token |
| `tool_start` | Tool node begins executing |
| `tool_end` | Tool node finishes |
| `progress` | Long-running step milestone (e.g. resume ingestion step N) |
| `hitl_request` | Agent pauses for user approval |
| `applied` | Application submitted successfully |
| `resume_ready` | Resume ingestion pipeline complete |
| `onboarding_required` | No resume on file |
| `captcha_blocked` | CAPTCHA detected during auto-apply |
| `login_required` | ATS requires account login |
| `done` | All processing complete |

Wire format — each event is a `data:` line followed by a blank line:

```
data: {"type":"tool_start","tool":"company_job_search"}

data: {"type":"token","content":"Here are"}

data: {"type":"done"}

```

> **Note:** The native browser `EventSource` API is GET-only. Because `/chat/stream` is a `POST`, the frontend must use `@microsoft/fetch-event-source` instead of the built-in `EventSource`.

---

## 7. Web Scraping & Job Search

### httpx

**Package:** `httpx`

Async HTTP client used for:
- Fetching job description pages (resume tailor tool)
- Scraping careers pages (job search tool)
- Fetching company info pages (company research tool)

### BeautifulSoup

**Package:** `beautifulsoup4`

HTML parser used alongside `httpx` to extract:
- Job title, location, snippet from careers pages
- Full job description text from a JD URL

### DuckDuckGo Search

**Package:** `duckduckgo-search`

Privacy-respecting, no-API-key search. Used in:
- **Job search tool:** discovers a company's official careers page URL; falls back to site-scoped search if JS-rendered
- **Company research tool:** pulls recent news, Glassdoor ratings, hiring/layoff signals

---

## 8. Resume Processing

### pypdf

**Package:** `pypdf`

Extracts raw text from uploaded PDF resumes. Used in `agent/resume/ingestion.py` as the first parse step.

### python-docx

**Package:** `python-docx`

Extracts raw text from uploaded DOCX resumes. Runs alongside `pypdf` — the parser is chosen based on the uploaded file extension.

### Jinja2

**Package:** `Jinja2`

Templating engine for the ATS resume HTML. The template (`agent/resume/templates/ats_resume.html`) is a fixed single-column layout with standard ATS-compliant section headings. GPT-4o-rewritten resume data is injected into the template before PDF conversion.

ATS compliance rules enforced by the template:
- Single column, no multi-column tables or text boxes
- Arial/Helvetica 11pt body, 14pt headings, 0.75 in margins
- All text in `<p>` / `<ul>` tags — fully selectable, no images
- Standard headings: Contact · Summary · Skills · Experience · Education · Certifications · Projects
- Plain bullets (`•` or `-`), no custom icons or graphics

### WeasyPrint

**Package:** `weasyprint`

Converts the Jinja2-rendered HTML → ATS-safe PDF. Output filenames follow the pattern:

```
<company>_<role>_<YYYYMMDD>_resume.pdf
```

Saved to `resumes/{user_id}/tailored/` (tailored) or `resumes/{user_id}/master/` (master).

---

## 9. Browser Automation — Playwright

**Package:** `playwright` (async API), `playwright-stealth`
**Browser:** headless Chromium (installed by `playwright install chromium`)
**Screenshot encoding:** `Pillow`

Used exclusively by the Auto-Apply tool (`agent/tools/auto_apply.py`).

### ATS Platform Detection

`agent/playwright/ats_detector.py` inspects the page URL and DOM to identify the ATS:

| Platform | Detection signal |
|----------|-----------------|
| Greenhouse | `greenhouse.io` or `boards.greenhouse.io` in URL |
| Lever | `jobs.lever.co` in URL |
| Workday | `myworkdayjobs.com` in URL |
| Taleo | `taleo.net` in URL |
| ICIMS | `icims.com` in URL |
| Generic | Heuristic `<input>` label inspection |

### Field Maps

Each ATS platform has a dedicated Python file in `agent/playwright/field_maps/` mapping profile fields to CSS selectors. `form_filler.py` loads the correct map and drives Playwright:

```python
page.fill(selector["First Name"], profile["full_name"].split()[0])
page.set_input_files(selector["Resume"], resume_pdf_path)
```

### Human-Like Delays

A random 1–3 second delay is added between each field interaction to reduce bot-detection fingerprinting. `playwright-stealth` suppresses additional automation signals.

### HITL Gates in the Pipeline

| Gate | Trigger |
|------|---------|
| `auto_apply` | Before Playwright touches any form field — shows pre-fill preview |
| `submit_application` | After all fields are filled — shows full-page screenshot |

---

## 10. Frontend

**Primary option:** Gradio (`gradio`)
**Alternative:** Plain HTML + JavaScript (`EventSource` via `@microsoft/fetch-event-source`)

### Screens

| Screen | File | Purpose |
|--------|------|---------|
| Onboarding | `frontend/onboarding.py` | Resume upload; chat locked until upload completes |
| Chat | `frontend/app.py` | SSE streaming chat, HITL approval cards |
| Dashboard | `frontend/dashboard.py` | Application tracker table, resume version history |

### HITL Card

When a `hitl_request` event arrives over SSE, the frontend renders an inline approval card:
- `write_resume` → gap analysis table + plain-text bullet preview
- `auto_apply` → table of all fields to be filled
- `submit_application` → full-page screenshot of the completed form

The user clicks **Approve / Edit / Reject**. The decision POSTs to `POST /chat/approve` and the agent resumes.

---

## 11. Observability — LangSmith

**Package:** `langsmith`
**Required env vars:**

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=<your_langsmith_key>
LANGCHAIN_PROJECT=jobhuntai
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

With `LANGCHAIN_TRACING_V2=true` set, every `graph.invoke()` and `graph.astream()` call is automatically traced — no manual instrumentation needed.

### What Is Captured Per Run

| Span type | Fields |
|-----------|--------|
| LLM call | prompt, completion, model, token counts, latency, cost estimate |
| Graph node | node name, input state, output state, latency |
| Tool call | tool name, input args, raw output, execution time |
| HITL event | action, decision (approve/edit/reject), correction text |
| Memory op | ChromaDB query, namespace, top-K chunks, similarity scores |
| Error | exception type, message, full stack trace on the failed span |

### Run Metadata Tags

Every run is tagged with `user_id`, `session_id`, and `run_type` (`chat | auto_apply | resume_tailor | job_search`) so traces can be filtered by user or workflow in LangSmith.

### User Feedback

After each response the frontend shows thumbs-up / thumbs-down. The vote posts to `POST /feedback` → `langsmith_client.create_feedback(run_id, score=1|0)`.

### Evaluation Pipeline

Production traces are promoted to the `jobhuntai-evals` dataset. Two evaluators run periodically:
- **Resume tailoring quality** — does the rewritten resume mirror JD keywords?
- **Job-match ranking accuracy** — do higher match scores correlate with applications the user pursued?

---

## 12. MCP Integration

**Server:** Official Filesystem MCP server (from Anthropic/MCP ecosystem)
**Wrapper tool:** `agent/tools/mcp_fs.py`

Used for reading and writing resume files and cover letter drafts to disk without the agent needing direct filesystem access. The MCP server runs as a sidecar process; the LangGraph tool calls it over the MCP protocol.

---

## 13. Configuration & Secrets

**Package:** `python-dotenv`
**File:** `.env` (gitignored) — template provided as `.env.example`

All secrets and environment-specific values live in `.env`:

```env
# LLM
OPENAI_API_KEY=sk-...

# Observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=jobhuntai
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

`.env.example` is committed to the repo with placeholder values so new contributors know exactly what to set.

---

## 14. Full Dependency List

| Package | Version constraint | Role |
|---------|--------------------|------|
| `fastapi` | `>=0.111` | HTTP + SSE gateway |
| `uvicorn[standard]` | `>=0.29` | ASGI server |
| `sse-starlette` | `>=1.8` | SSE response class |
| `langgraph` | `>=0.2` | Agent StateGraph |
| `langchain-core` | `>=0.2` | Base message types, tool schema |
| `langsmith` | `>=0.1` | Tracing + evaluation |
| `openai` | `>=1.30` | GPT-4o calls + streaming |
| `chromadb` | `>=0.5` | Vector store |
| `sentence-transformers` | `>=2.7` | Local embeddings |
| `sqlite3` | built-in | Relational store |
| `httpx` | `>=0.27` | Async HTTP client |
| `beautifulsoup4` | `>=4.12` | HTML parsing |
| `duckduckgo-search` | `>=6.0` | Web + job search |
| `pypdf` | `>=4.2` | PDF text extraction |
| `python-docx` | `>=1.1` | DOCX text extraction |
| `Jinja2` | `>=3.1` | ATS resume template rendering |
| `weasyprint` | `>=61` | HTML → PDF conversion |
| `playwright` | `>=1.44` | Browser automation |
| `playwright-stealth` | `>=1.0` | Bot-detection suppression |
| `Pillow` | `>=10.3` | Screenshot encoding |
| `gradio` | `>=4.30` | Frontend UI (optional) |
| `python-dotenv` | `>=1.0` | `.env` loading |
| `python-multipart` | `>=0.0.9` | File upload parsing in FastAPI |
