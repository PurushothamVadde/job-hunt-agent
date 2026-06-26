# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**JobHuntAI** — a multi-session AI job search assistant. Users upload a master resume once; the agent extracts their career profile, learns across sessions, tailors ATS-safe PDFs per job, and automates applications — all through a streaming chat interface.

The repository currently contains **documentation only**. No application code exists yet. Implementation should follow the architecture defined in `docs/architecture/`.

## Intended Project Layout

```
job-hunt-agent/
├── agent/
│   ├── graph.py                    # LangGraph StateGraph — all nodes and edges
│   ├── memory.py                   # Session-start retrieval + session-end write
│   ├── tools/
│   │   ├── rag.py
│   │   ├── company_job_search.py
│   │   ├── resume_tailor.py
│   │   ├── company_research.py
│   │   ├── auto_apply.py
│   │   └── mcp_fs.py
│   ├── resume/
│   │   ├── ingestion.py
│   │   ├── tailoring.py
│   │   ├── pdf_generator.py
│   │   └── templates/ats_resume.html
│   └── playwright/
│       ├── ats_detector.py
│       ├── form_filler.py
│       └── field_maps/{greenhouse,lever,workday,generic}.py
├── api/
│   ├── chat.py                     # POST /chat/stream, POST /chat/approve
│   ├── resume.py
│   ├── sessions.py
│   ├── documents.py
│   └── applications.py
├── db/
│   ├── sqlite.py                   # All DDL + CRUD for every table
│   └── chroma.py                   # ChromaDB wrapper (upsert / query / delete by namespace)
├── observability/
│   └── langsmith.py                # Client init, metadata tagging, feedback, /admin/traces
├── frontend/
│   ├── app.py
│   ├── onboarding.py
│   └── dashboard.py
└── .env                            # Never committed; see .env.example
```

## Development Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # fill in OPENAI_API_KEY + LangSmith vars
uvicorn api.main:app --reload
```

## Running / Testing

No test suite exists yet. When adding one, run individual tests with:

```bash
pytest tests/path/to/test_file.py::test_function_name -v
```

## Required Environment Variables

```env
OPENAI_API_KEY=...
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=jobhuntai
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

## Architecture — What to Know Before Writing Code

### Agent loop (`agent/graph.py`)

A LangGraph `StateGraph` runs on every user message:

```
plan → tool_select → tool_execute → synthesize → respond
```

`tool_execute` loops back to `tool_select` until all sub-goals are resolved. Post-turn nodes (`auto-summarize → SQLite write → ChromaDB write → LangSmith flush`) run after `respond`. Full state shape is in [docs/architecture/02-agent-orchestration.md](docs/architecture/02-agent-orchestration.md).

### Memory (`agent/memory.py`)

Two tiers that serve different purposes:

| Tier | Store | What it holds |
|------|-------|---------------|
| Episodic | SQLite `episodic_memories` | Auto-generated turn summaries, queried by date |
| Semantic | ChromaDB `memory:{user_id}` | Career facts extracted by GPT-4o, queried by cosine similarity |

At session start, both are queried and merged into the system prompt. At session end, both are written. Resume chunks live in `resume:{user_id}`; uploaded docs in `docs:{user_id}`.

### HITL (`agent/graph.py` + `api/chat.py`)

Four gates use LangGraph `interrupt()` to pause graph execution:

| Gate | Trigger |
|------|---------|
| `write_resume` | Before writing tailored PDF to disk |
| `auto_apply` | Before Playwright touches any form field |
| `submit_application` | Before clicking Submit |
| `send_followup` | *(planned)* Before sending a follow-up email |

The graph suspends, emits a `hitl_request` SSE event, and waits for `POST /chat/approve`. `api/chat.py` injects the decision into state and calls `graph.resume()`.

### SSE Streaming (`api/chat.py`)

`POST /chat/stream` returns `text/event-stream`. The backend yields a mix of `token`, `tool_start`, `tool_end`, `hitl_request`, `progress`, and `done` events from a single `graph.astream()` call. The frontend must use `@microsoft/fetch-event-source` (not native `EventSource`) because the endpoint is a POST.

### Resume Ingestion (`agent/resume/ingestion.py`)

Six-step pipeline triggered by `POST /resume/upload`, all progress streamed over SSE:
1. Parse PDF (`pypdf`) or DOCX (`python-docx`)
2. GPT-4o structured extraction → canonical JSON
3. Persist to SQLite `resume_profiles`
4. Chunk (512 tok / 50 tok overlap) + embed → ChromaDB `resume:{user_id}`
5. Regenerate ATS master PDF via WeasyPrint + Jinja2
6. Extract semantic career facts → ChromaDB `memory:{user_id}`

Until step 6 completes, all chat messages return `onboarding_required`.

### ATS PDF Rules (`agent/resume/templates/ats_resume.html`)

Single column · Arial/Helvetica 11pt · 0.75 in margins · all text in `<p>`/`<ul>` · standard section order (Contact → Summary → Skills → Experience → Education → Certifications → Projects) · no graphics, columns, or headers/footers.

### Playwright Auto-Apply (`agent/tools/auto_apply.py`)

Detect ATS platform → map profile fields to CSS selectors → HITL Gate 1 (pre-fill preview) → `page.fill()` / `page.set_input_files()` with 1–3 s human-like delays → HITL Gate 2 (screenshot review) → submit → write to `applications` table. Errors (`captcha_blocked`, `login_required`, `unknown_form`) are streamed as SSE events; the pipeline does not throw.

### Module Dependencies

Key non-obvious dependency: `api/documents.py` → `db/chroma.py` directly (not through `agent/tools/rag.py`). The RAG tool is a query-time consumer; the ingestion write path goes document → chroma. See [docs/architecture/12-modules.md](docs/architecture/12-modules.md) for the full graph.

## Key Docs

- [docs/architecture/README.md](docs/architecture/README.md) — index + key design decisions
- [docs/tools-and-technology.md](docs/tools-and-technology.md) — full library reference with version constraints
- [docs/architecture/10-api.md](docs/architecture/10-api.md) — complete endpoint reference
- [docs/architecture/03-persistence.md](docs/architecture/03-persistence.md) — SQLite schema (ER diagram)
