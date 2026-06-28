# JobHuntAI — Development Log

## [Unreleased] — Active Development

---

## Session 3 — 2026-06-26: Frontend Stabilization

### Added
- **Chainlit SQLite data layer** (`db/chainlit_data_layer.py`) — implements `BaseDataLayer` so Chainlit's thread sidebar persists to our own SQLite DB instead of requiring PostgreSQL
- **Custom JS logo swap** (`public/custom.js`) — MutationObserver-based swap replaces Chainlit's diamond logo with the JobHuntAI SVG after React renders
- **Dark mode logo** (`public/logo_dark.svg`) — companion asset for dark theme
- **`@cl.data_layer` registration** — uses the correct Chainlit 2.x decorator pattern (factory function) instead of the deprecated direct assignment

### Changed
- `db/sqlite.py` — `create_session()` now accepts optional `session_id` parameter and uses `INSERT OR IGNORE` to support lazy creation pattern
- `chainlit_app.py` — sessions created lazily on first message (not on every page load), preventing empty "New session" flood in sidebar
- `chainlit_app.py` — `on_chat_start` / `on_message` guard against `None` user (session expiry)
- `chainlit_app.py` — empty partial message no longer sent before HITL prompt
- `db/chainlit_data_layer.py` — `create_step` normalizes Chainlit's `"user_message"` type to `"user"` before DB write so `_load_steps` round-trips correctly
- `db/chainlit_data_layer.py` — `get_favorite_steps` / `set_step_favorite` fixed to match `BaseDataLayer` signatures
- `requirements.txt` — replaced `gradio>=4.30.0` with `chainlit>=2.11.1`, `aiofiles>=23.0.0`, `asyncpg>=0.29.0`
- `.chainlit/config.toml` — sidebar open by default, custom JS registered, logo URL corrected to `/logo.svg`
- `chainlit.md` — emptied to remove the "Readme" button from the top bar

### Fixed
- `DATABASE_URL` env var conflict: Chainlit reads this as a PostgreSQL DSN; renamed to `SQLITE_DB_PATH` in `.env` and updated `db/sqlite.py` to match
- `asyncpg.exceptions.ClientConfigurationError` on login — root cause was the DSN conflict above
- Thread sidebar not appearing — required `@cl.data_layer` with a working implementation; `default_sidebar_state = "open"` alone is not enough without a data layer
- `PageInfo` missing `startCursor` field causing `PaginatedResponse` validation error
- `sqlite3.IntegrityError: FOREIGN KEY constraint failed` in `create_step` — data layer was called before the session row existed; fixed with session existence check

---

## Session 2 — 2026-06-25: Auth & Frontend Overhaul

### Added
- **Chainlit frontend** (`chainlit_app.py`) — full replacement of the Gradio UI with Chainlit 2.11.1, providing OAuth login, streaming chat, resume upload, HITL approval, and `/dashboard` command
- **Google + GitHub OAuth** via `@cl.oauth_callback` — no custom auth routes needed
- **`public/logo.svg` + `public/logo_light.svg`** — JobHuntAI orange SVG text logo
- **`public/custom.css`** — CSS overrides for Chainlit branding
- **`.chainlit/config.toml`** — app name "JobHuntAI", file upload config (PDF/DOCX, 20 MB), CoT display
- **`api/oauth.py`** + **`api/oauth_routes.py`** — earlier FastAPI-based OAuth attempt (superseded by Chainlit's built-in OAuth)
- **`db/sqlite.py`** — `get_or_create_oauth_user()` for OAuth login path; schema extended with `oauth_provider`, `display_name`, `picture_url` columns; `hashed_password` made nullable

### Changed
- `requirements.txt` — added `chainlit`, `asyncpg`
- `db/sqlite.py` — `DATABASE_URL` env var renamed to `SQLITE_DB_PATH` to avoid conflict with Chainlit's PostgreSQL data layer detection

### Fixed
- `ModuleNotFoundError: No module named 'asyncpg'` — Chainlit 2.11.1 imports its data layer module unconditionally at auth time
- Google OAuth `Error 400: redirect_uri_mismatch` — updated Google Cloud Console to `http://localhost:8000/auth/oauth/google/callback`
- GitHub OAuth "redirect_uri not associated" — updated GitHub OAuth App to `http://localhost:8000/auth/oauth/github/callback`

### Removed
- `frontend/app.py` Gradio auth card (login/register forms)
- `frontend/dashboard.py`, `frontend/onboarding.py` — superseded by Chainlit pages
- `api/auth.py`, `api/chat.py`, `api/resume.py`, `api/sessions.py`, `api/applications.py`, `api/documents.py` — superseded by Chainlit + new route structure

---

## Session 1 — 2026-06-24: Core Backend Build

### Added

#### Agent
- `agent/orchestrator/graph.py` — LangGraph `StateGraph`: `plan → tool_select → tool_execute → synthesize → respond`, with post-turn `auto_summarize` node
- `agent/orchestrator/nodes.py` — all node implementations (planner, selector, executor, synthesizer, responder)
- `agent/orchestrator/state.py` — `AgentState` TypedDict with full state shape
- `agent/llm/client.py` — `complete_json()` / `complete_text()` wrappers over `langchain-openai`
- `agent/memory/loader.py` — `load_memories()`: queries episodic (SQLite) + semantic (ChromaDB) at session start
- `agent/memory/saver.py` — `save_memories()`: writes episodic summary + semantic facts at session end
- `agent/memory/prompt.py` — `build_memory_prompt()`: formats memory list into system prompt block
- `agent/resume/parser.py` — `parse_document()`: PDF (pypdf) + DOCX (python-docx) text extraction
- `agent/resume/extractor.py` — `extract_profile()` + `extract_facts()`: GPT-4o structured extraction to canonical JSON
- `agent/resume/embedder.py` — `embed_resume_chunks()` + `embed_career_facts()`: chunk + embed to ChromaDB
- `agent/resume/pipeline.py` — `ingest()`: async generator orchestrating all 6 ingestion steps with SSE-style progress events
- `agent/resume/pdf_generator.py` — `generate_ats_pdf()`: WeasyPrint + Jinja2 ATS-safe PDF generation
- `agent/resume/templates/ats_resume.html` — single-column Arial 11pt ATS-safe HTML template
- `agent/resume/tailoring.py` — `tailor_resume()`: GPT-4o job-description-aware resume tailoring
- `agent/tools/rag.py` — `RAGTool`: semantic search over user's resume + uploaded docs
- `agent/tools/company_job_search.py` — `CompanyJobSearchTool`: DuckDuckGo + BeautifulSoup job listing search
- `agent/tools/company_research.py` — `CompanyResearchTool`: company background research
- `agent/tools/resume_tailor.py` — `ResumeTailorTool`: wraps tailoring pipeline as an agent tool
- `agent/tools/auto_apply.py` — `AutoApplyTool`: Playwright-based form detection and filling with HITL gates
- `agent/tools/mcp_fs.py` — `MCPFileSystemTool`: file read/write via MCP filesystem protocol
- `agent/playwright/ats_detector.py` — ATS platform detection (Greenhouse, Lever, Workday, generic)
- `agent/playwright/form_filler.py` — field mapping + human-like fill delays
- `agent/playwright/field_maps/` — CSS selector maps for Greenhouse, Lever, Workday, generic

#### API
- `api/main.py` — FastAPI app with lifespan startup (SQLite init, ChromaDB init)
- `api/graph_runner.py` — `run_graph()` helper for streaming agent execution
- `api/routes/chat.py` — `POST /chat/stream`, `POST /chat/approve` (HITL)
- `api/routes/resume.py` — `POST /resume/upload` with SSE progress streaming
- `api/auth/routes.py` — JWT-based auth routes (pre-OAuth, superseded in Session 2)
- `api/sse_events.py` — SSE event type constants

#### Database
- `db/sqlite.py` — full DDL + CRUD: `users`, `sessions`, `messages`, `resume_profiles`, `episodic_memories`, `applications`
- `db/chroma.py` — ChromaDB wrapper: upsert / query / delete by namespace (`resume:`, `memory:`, `docs:`)
- `db/repositories.py` — higher-level repository helpers over raw sqlite helpers

#### Observability
- `observability/langsmith.py` — LangSmith client init, trace metadata tagging, feedback helpers

#### Docs
- `docs/architecture/` — 12 architecture docs covering agent orchestration, persistence, API, modules, HITL, SSE, resume pipeline, ATS PDF rules, Playwright apply flow
- `docs/tools-and-technology.md` — full library reference with version constraints
- `CLAUDE.md` — project guidance for Claude Code

---

## Project Overview

| Layer | Technology |
|---|---|
| Frontend | Chainlit 2.11.1 (OAuth, streaming chat, HITL, sidebar) |
| Agent | LangGraph StateGraph + LangChain tools |
| LLM | GPT-4o via `langchain-openai` |
| Persistence | SQLite (structured) + ChromaDB (vector) |
| Auth | Google + GitHub OAuth via Chainlit |
| Resume PDF | WeasyPrint + Jinja2 |
| Auto-Apply | Playwright with HITL gates |
| Observability | LangSmith |
| Runtime | Python 3.12, uvicorn / chainlit CLI |
