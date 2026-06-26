# Module Dependency Map

Shows which Python modules own which responsibilities and how they depend on each other. Arrows mean "imports / calls".

## Dependency Graph

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#e0e7ff', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#eef2ff'}}}%%
flowchart TD
    subgraph API["api/"]
        AC["chat.py\nSSE endpoint + chat/approve"]
        AR["resume.py\nupload · current · versions"]
        AS["sessions.py\nCRUD"]
        AD["documents.py\nupload + ingest"]
        AA["applications.py\ntracker CRUD"]
    end

    subgraph AgentCore["agent/"]
        GRAPH["graph.py\nStateGraph — all nodes"]
        MEM["memory.py\nread/write episodic + semantic"]
    end

    subgraph Tools["agent/tools/"]
        TR["rag.py\nRAG over career docs"]
        TC["company_job_search.py\ndiscovery · scrape · rank"]
        TT["resume_tailor.py\ngap analysis · rewrite · PDF"]
        TCR["company_research.py\nDuckDuckGo + web"]
        TA["auto_apply.py\nPlaywright pipeline"]
        TM["mcp_fs.py\nFilesystem MCP wrapper"]
    end

    subgraph ResumeModule["agent/resume/"]
        RI["ingestion.py\nparse · extract · chunk · embed"]
        RTL["tailoring.py\ngap analysis + bullet rewrite"]
        RP["pdf_generator.py\nJinja2 render + WeasyPrint"]
        RTPL["templates/ats_resume.html\nATS-safe HTML template"]
    end

    subgraph PlaywrightModule["agent/playwright/"]
        PD["ats_detector.py\nURL + DOM fingerprinting"]
        PF["form_filler.py\nfield mapping + fill logic"]
        PM["field_maps/\ngreenhouse · lever · workday · generic"]
    end

    subgraph DB["db/"]
        DS["sqlite.py\nall DDL + CRUD helpers"]
        DC["chroma.py\nupsert · query · delete by namespace"]
    end

    OBS["observability/langsmith.py\nclient init · metadata · feedback"]

    AC --> GRAPH
    AR --> RI
    AD --> DC

    GRAPH --> MEM
    GRAPH --> TR & TC & TT & TCR & TA & TM
    GRAPH --> OBS

    MEM --> DS & DC
    TR --> DC
    TC --> DC
    TT --> RTL
    RTL --> RP
    RP --> RTPL
    TA --> PD
    PD --> PF
    PF --> PM
    RI --> DS & DC
```

## Module Responsibilities

### `api/`

| Module | Owns |
|--------|------|
| `chat.py` | `POST /chat/stream` SSE endpoint, `POST /chat/approve` HITL resume |
| `resume.py` | `POST /resume/upload`, `GET /resume/current`, `GET /resume/versions`, `DELETE /resume/versions/{id}` |
| `sessions.py` | Session list, get, delete |
| `documents.py` | `POST /documents/upload` — triggers RAG indexing |
| `applications.py` | Application tracker CRUD, `GET /applications`, `PATCH /applications/{id}` |

### `agent/`

| Module | Owns |
|--------|------|
| `graph.py` | Full LangGraph `StateGraph` definition — all nodes, edges, interrupt points |
| `memory.py` | Session-start memory retrieval (SQLite + ChromaDB); session-end summarize + store |

### `agent/tools/`

| Module | Tool | External deps |
|--------|------|--------------|
| `rag.py` | RAG over career docs | ChromaDB |
| `company_job_search.py` | Job search + ranking | DuckDuckGo, httpx, BeautifulSoup, sentence-transformers, ChromaDB |
| `resume_tailor.py` | JD gap analysis + rewrite + PDF | OpenAI API, `agent/resume/` modules |
| `company_research.py` | Company news + Glassdoor | DuckDuckGo, httpx |
| `auto_apply.py` | Playwright auto-apply | `agent/playwright/` modules |
| `mcp_fs.py` | File read/write | Filesystem MCP server |

### `agent/resume/`

| Module | Owns |
|--------|------|
| `ingestion.py` | pypdf/python-docx parse → GPT-4o structured extraction → chunk + embed → SQLite + ChromaDB |
| `tailoring.py` | Gap analysis (`matched/missing_required/missing_preferred`) + GPT-4o bullet rewrite |
| `pdf_generator.py` | Jinja2 template render + WeasyPrint HTML→PDF conversion |
| `templates/ats_resume.html` | ATS-compliant single-column HTML template |

### `agent/playwright/`

| Module | Owns |
|--------|------|
| `ats_detector.py` | URL pattern + DOM inspection to identify ATS platform |
| `form_filler.py` | Load platform field map, call `page.fill()` / `page.set_input_files()` |
| `field_maps/greenhouse.py` | Greenhouse CSS selector definitions |
| `field_maps/lever.py` | Lever CSS selector definitions |
| `field_maps/workday.py` | Workday CSS selector definitions |
| `field_maps/generic.py` | Heuristic fallback selectors |

### `db/`

| Module | Owns |
|--------|------|
| `sqlite.py` | All SQLite DDL, migrations, and CRUD for every table |
| `chroma.py` | ChromaDB client wrapper — upsert documents, similarity query, delete by namespace |

### `observability/`

| Module | Owns |
|--------|------|
| `langsmith.py` | LangSmith client init, run metadata tagging, feedback helper, trace proxy for `/admin/traces` |

## Project Directory Layout

```
job-hunt-agent/
├── agent/
│   ├── graph.py
│   ├── memory.py
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
│   │   └── templates/
│   │       └── ats_resume.html
│   └── playwright/
│       ├── ats_detector.py
│       ├── form_filler.py
│       └── field_maps/
│           ├── greenhouse.py
│           ├── lever.py
│           ├── workday.py
│           └── generic.py
├── api/
│   ├── chat.py
│   ├── resume.py
│   ├── sessions.py
│   ├── documents.py
│   └── applications.py
├── db/
│   ├── sqlite.py
│   └── chroma.py
├── observability/
│   └── langsmith.py
├── resumes/
│   └── {user_id}/
│       ├── master/
│       └── tailored/
├── frontend/
│   ├── app.py
│   ├── onboarding.py
│   └── dashboard.py
└── .env.example
```
