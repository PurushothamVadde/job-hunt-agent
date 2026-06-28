# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**JobHuntAI** — a multi-session AI job search assistant. Users upload a master resume once; the agent extracts their career profile, learns across sessions, tailors ATS-safe PDFs per job, and automates applications — all through a Chainlit streaming chat interface.

## Project Layout

```
job-hunt-agent/
├── chainlit_app.py                 # Entry point — Chainlit app, auth, message loop
├── chainlit.md                     # Chainlit welcome screen content
├── agent/
│   ├── orchestrator/
│   │   ├── graph.py                # LangGraph StateGraph — builds the compiled graph
│   │   ├── nodes.py                # All node implementations (plan, select, execute, synthesize)
│   │   ├── routing.py              # Edge routing logic
│   │   └── state.py                # AgentState TypedDict + SYSTEM_PROMPT
│   ├── llm/
│   │   ├── client.py               # complete_json / complete_text helpers
│   │   └── provider.py             # ChatOpenAI model init
│   ├── memory/
│   │   ├── loader.py               # Session-start: load episodic + semantic memories
│   │   ├── saver.py                # Session-end: write summaries + career facts
│   │   └── prompt.py               # build_memory_prompt() for system context
│   ├── tools/
│   │   ├── __init__.py             # TOOLS + HITL_TOOLS registries, tool_descriptions()
│   │   ├── rag.py
│   │   ├── company_job_search.py
│   │   ├── resume_tailor.py
│   │   ├── company_research.py
│   │   ├── auto_apply.py
│   │   └── mcp_fs.py
│   ├── resume/
│   │   ├── pipeline.py             # ingest() — full 6-step ingestion pipeline
│   │   ├── parser.py               # PDF / DOCX → raw text
│   │   ├── extractor.py            # GPT-4o structured extraction → canonical JSON
│   │   ├── embedder.py             # Chunk + embed → ChromaDB
│   │   ├── ingestion.py            # Legacy entry point (imports from pipeline)
│   │   ├── tailoring.py            # JD → tailored profile delta
│   │   ├── pdf_generator.py        # WeasyPrint + Jinja2 → ATS PDF
│   │   └── templates/ats_resume.html
│   └── playwright/
│       ├── ats_detector.py
│       ├── form_filler.py
│       ├── workday_apply.py        # browser-use Agent for Workday
│       └── field_maps/{greenhouse,lever,workday,generic}.py
├── db/
│   ├── sqlite.py                   # All DDL + CRUD for every table
│   ├── chroma.py                   # ChromaDB wrapper (upsert / query / delete by namespace)
│   └── chainlit_data_layer.py      # BaseDataLayer impl — persists threads/steps to SQLite
├── observability/
│   └── langsmith.py
├── public/                         # Static assets served by Chainlit
│   ├── custom.css
│   ├── custom.js
│   └── *.svg
└── .env                            # Never committed; see .env.example
```

## Development Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # fill in keys
PYTHONUNBUFFERED=1 chainlit run chainlit_app.py
```

## Required Environment Variables

```env
OPENAI_API_KEY=...
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=jobhuntai
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
OAUTH_GOOGLE_CLIENT_ID=...
OAUTH_GOOGLE_CLIENT_SECRET=...
CHAINLIT_AUTH_SECRET=...
```

## Architecture — What to Know Before Writing Code

### Entry point (`chainlit_app.py`)

Chainlit handles OAuth, session management, and message streaming. Key hooks:
- `@cl.oauth_callback` — Google OAuth → creates/updates user in SQLite
- `@cl.on_chat_start` — loads memories, sets up session
- `@cl.on_message` — runs the LangGraph agent, handles HITL approvals
- `SQLiteDataLayer` registered via `@cl.data_layer` — persists threads and messages for sidebar history

### Agent loop (`agent/orchestrator/`)

A LangGraph `StateGraph` runs on every user message:

```
plan → tool_select → tool_execute → synthesize → respond
```

`tool_execute` loops back to `tool_select` until all sub-goals are resolved. HITL interrupts pause the graph; `chainlit_app.py` resumes it with the user's decision via `Command(resume=...)`.

### HITL gates (`agent/orchestrator/nodes.py`)

| Gate | Trigger |
|------|---------|
| `write_resume` | Before writing tailored PDF to disk |
| `auto_apply` | Before Playwright fills any form field (skipped for `phase=plan`) |
| `submit_application` | Before clicking Submit |

### Memory (`agent/memory/`)

Two tiers:

| Tier | Store | What it holds |
|------|-------|---------------|
| Episodic | SQLite `episodic_memories` | Auto-generated turn summaries |
| Semantic | ChromaDB `memory:{user_id}` | Career facts extracted by GPT-4o |

### Resume Ingestion (`agent/resume/pipeline.py`)

Six-step pipeline triggered by file upload:
1. Parse PDF/DOCX → raw text
2. GPT-4o structured extraction → canonical JSON
3. Persist to SQLite `resume_profiles`
4. Chunk + embed → ChromaDB `resume:{user_id}`
5. Regenerate ATS master PDF (WeasyPrint + Jinja2)
6. Extract semantic career facts → ChromaDB `memory:{user_id}`

Until step 6 completes, chat returns `onboarding_required`.

### Workday Auto-Apply (`agent/playwright/workday_apply.py`)

Uses **browser-use** Agent (not raw Playwright selectors). Two phases:
- `run_fill_agent` — navigates, signs in via `sensitive_data`, uploads resume, fills all steps, stops at Review
- `run_submit_agent` — reopens, clicks Submit

browser-use always copies `user_data_dir` to a temp path — persistent login is not possible. Credentials must be passed via `sensitive_data` each run.

### Session history (`db/chainlit_data_layer.py`)

- `create_step` — saves user messages only
- `update_step` — saves final assistant responses (called when Chainlit finalizes a streamed message)
- `upsert_assistant_step` in sqlite.py uses `step_uuid` column to update in-place rather than inserting duplicates

## Key Docs

- [docs/architecture/README.md](docs/architecture/README.md) — index + key design decisions
- [docs/tools-and-technology.md](docs/tools-and-technology.md) — full library reference
- [docs/architecture/03-persistence.md](docs/architecture/03-persistence.md) — SQLite schema
