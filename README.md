# JobHuntAI

An AI-powered job search assistant that learns your career profile, tailors ATS-safe resumes per role, and automates Workday applications — all through a streaming chat interface.

![JobHuntAI](public/JobHuntAI_logo.svg)

---

## Features

- **Resume ingestion** — Upload a PDF or DOCX once; GPT-4o extracts a structured career profile, builds a ChromaDB semantic index, and generates a clean ATS-safe master PDF
- **Persistent memory** — Episodic (session summaries) and semantic (career facts) memories are recalled across sessions so the agent knows you better over time
- **Job search** — Crawls company careers pages via Crawl4AI with multi-strategy extraction (JSON-LD → embedded JSON → HTML anchors → markdown → GPT fallback); ranks results against your profile
- **Resume tailoring** — Generates a job-specific delta, rewrites your resume with matched skills highlighted, and produces a tailored ATS PDF via WeasyPrint + Jinja2
- **Workday auto-apply** — browser-use Agent fills all application steps (My Information, My Experience, Application Questions, Voluntary Disclosures) and stops at Review for your approval before submitting
- **HITL gates** — Human-in-the-loop approval before writing a PDF, before filling any form, and before clicking Submit
- **Session history** — Full conversation history persisted to SQLite; previous threads accessible from the Chainlit sidebar

---

## Architecture

```
User (browser)
    │
    ▼
Chainlit (chainlit_app.py)          ← OAuth, streaming, HITL, file upload
    │
    ▼
LangGraph StateGraph                ← plan → tool_select → tool_execute → synthesize → respond
    │
    ├── agent/orchestrator/         ← graph, nodes, routing, state
    ├── agent/memory/               ← episodic (SQLite) + semantic (ChromaDB) memory
    ├── agent/tools/                ← rag, company_job_search, resume_tailor, auto_apply, mcp_fs
    ├── agent/resume/               ← parse → extract → embed → pdf_generator → tailoring
    └── agent/playwright/           ← job_scraper (Crawl4AI), workday_apply (browser-use)
         │
         ▼
    db/sqlite.py   db/chroma.py     ← persistence
```

---

## Tech Stack

| Layer | Library |
|---|---|
| Chat UI | [Chainlit](https://github.com/Chainlit/chainlit) |
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| LLM | OpenAI GPT-4o |
| Observability | [LangSmith](https://smith.langchain.com) |
| Vector store | [ChromaDB](https://github.com/chroma-core/chroma) |
| Web crawling | [Crawl4AI](https://github.com/unclecode/crawl4ai) |
| Browser automation | [browser-use](https://github.com/browser-use/browser-use) |
| Resume PDF | [WeasyPrint](https://weasyprint.org) + Jinja2 |
| Database | SQLite |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Google OAuth credentials (for Chainlit login)
- OpenAI API key
- LangSmith API key (optional, for tracing)

### 1. Clone and install

```bash
git clone https://github.com/PurushothamVadde/job-hunt-agent.git
cd job-hunt-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-...

# Google OAuth (create at console.cloud.google.com)
OAUTH_GOOGLE_CLIENT_ID=...
OAUTH_GOOGLE_CLIENT_SECRET=...

# Chainlit auth secret (any random string)
CHAINLIT_AUTH_SECRET=...

# LangSmith tracing (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=jobhuntai
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

### 3. Run

```bash
PYTHONUNBUFFERED=1 chainlit run chainlit_app.py
```

Open [http://localhost:8000](http://localhost:8000), sign in with Google, and upload your resume.

---

## Usage

Once your resume is uploaded and processed:

```
You: Find senior software engineer jobs at CVS in Dallas

Agent: Here are 4 roles ranked by fit...

You: Apply for the Sr. Software Development Engineer role

Agent: [tailors resume, shows gap analysis, asks for approval]
       → Approve writing PDF?  ✓ Approve

Agent: Resume tailored. Proceed with application?
       → Approve auto-fill?  ✓ Approve

Agent: [browser-use fills Workday form, stops at Review page, takes screenshot]
       Form is filled and ready. Approve submission?
       → Approve submit?  ✓ Approve

Agent: Application submitted ✓
```

---

## Project Structure

```
job-hunt-agent/
├── chainlit_app.py              # Entry point — Chainlit hooks, OAuth, message loop
├── chainlit.md                  # Welcome screen content
├── agent/
│   ├── orchestrator/            # LangGraph graph, nodes, routing, AgentState
│   ├── llm/                     # complete_json / complete_text helpers
│   ├── memory/                  # Episodic + semantic memory load/save
│   ├── tools/                   # All tool modules + HITL registry
│   ├── resume/                  # Ingestion pipeline, tailoring, PDF generation
│   └── playwright/              # Job scraper (Crawl4AI), Workday automation (browser-use)
├── db/
│   ├── sqlite.py                # All DDL + CRUD
│   ├── chroma.py                # ChromaDB wrapper
│   └── chainlit_data_layer.py   # Chainlit BaseDataLayer → SQLite
├── observability/
│   └── langsmith.py
├── public/                      # Static assets served by Chainlit
└── docs/                        # Architecture docs and flow diagrams
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `OAUTH_GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID |
| `OAUTH_GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `CHAINLIT_AUTH_SECRET` | Yes | Random secret for Chainlit session signing |
| `LANGCHAIN_TRACING_V2` | No | Enable LangSmith tracing (`true`) |
| `LANGCHAIN_API_KEY` | No | LangSmith API key |
| `LANGCHAIN_PROJECT` | No | LangSmith project name |
| `SQLITE_DB_PATH` | No | SQLite file path (default: `jobhuntai.db`) |
| `CHROMA_PERSIST_DIR` | No | ChromaDB directory (default: `.chroma`) |
| `RESUMES_DIR` | No | Output directory for PDFs (default: `resumes`) |

---

## Contributing

1. Fork the repo and create a feature branch
2. Make your changes — keep commits focused
3. Open a pull request with a clear description of what changed and why

Bug reports and feature requests are welcome via [GitHub Issues](https://github.com/PurushothamVadde/job-hunt-agent/issues).

---

## License

MIT
