# JobHuntAI — Architecture Docs

Each document covers one focused area of the system. Start with the overview, then read whichever section is relevant to what you are building or debugging.

## Index

| # | Document | Topic |
|---|----------|-------|
| 1 | [01-overview.md](01-overview.md) | Full system map — all layers and connections |
| 2 | [02-agent-orchestration.md](02-agent-orchestration.md) | LangGraph ReAct loop, session init, post-turn memory |
| 3 | [03-persistence.md](03-persistence.md) | Two-tier memory (SQLite + ChromaDB), database schema |
| 4 | [04-resume-ingestion.md](04-resume-ingestion.md) | Resume upload → parse → embed → ATS PDF pipeline |
| 5 | [05-resume-tailoring.md](05-resume-tailoring.md) | JD gap analysis → GPT-4o rewrite → ATS PDF export |
| 6 | [06-job-search.md](06-job-search.md) | Careers page discovery → scrape → resume-match ranking |
| 7 | [07-auto-apply.md](07-auto-apply.md) | Playwright ATS detection → form fill → two HITL gates |
| 8 | [08-hitl.md](08-hitl.md) | Human-in-the-loop approval state machine |
| 9 | [09-streaming.md](09-streaming.md) | SSE token stream, tool events, HITL interrupts |
| 10 | [10-api.md](10-api.md) | All REST + SSE endpoints |
| 11 | [11-observability.md](11-observability.md) | LangSmith tracing, spans, feedback, datasets |
| 12 | [12-modules.md](12-modules.md) | Python module dependency map |

## Quick Reference

```
User → Frontend → FastAPI → LangGraph Agent → Tools → GPT-4o / DB / Web
                                    ↕
                          SQLite (episodic) + ChromaDB (semantic)
                                    ↕
                              LangSmith (traces)
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| LangGraph StateGraph | Explicit node graph makes tool sequencing and HITL interrupts easy to reason about |
| Two-tier memory | SQLite for structured episodic history; ChromaDB for semantic similarity retrieval |
| SSE streaming | Users see tokens and tool events in real time without polling |
| HITL before every write | Resume writes, form fills, and submissions never happen without explicit user approval |
| sentence-transformers (local) | Free, no API quota, runs offline for resume-match ranking |
| WeasyPrint + Jinja2 | Produces ATS-compliant single-column PDFs from a fixed HTML template |
