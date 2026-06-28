# Resume Upload Flow

## Overview

When a user uploads a resume (PDF or DOCX), the system runs a **six-step ingestion pipeline** that turns raw bytes into a structured career profile, searchable vector embeddings, an ATS-safe PDF, and semantic career facts — all streamed back to the client as live progress events over SSE.

---

## API Entry Point

**Endpoint:** `POST /resume/upload`  
**File:** `api/routes/resume.py`

The client sends a multipart form upload. The endpoint:

1. Reads the raw bytes from the upload
2. Validates the user via JWT Bearer (`get_current_user`)
3. Returns a **Server-Sent Events (SSE) stream** so the client receives live progress instead of waiting for a single response

```
Client                        FastAPI /resume/upload
  |-- multipart file upload -->|
  |<-- SSE: "Parsing document" |
  |<-- SSE: "Extracting..."    |
  |<-- SSE: "Persisting..."    |
  |<-- SSE: "Embedding..."     |
  |<-- SSE: "Generating PDF"   |
  |<-- SSE: "Extracting facts" |
  |<-- SSE: done { profile }   |
```

---

## Pipeline Orchestrator

**File:** `agent/resume/pipeline.py` — `ingest(user_id, filename, raw_bytes)`

An async generator that `yield`s a progress event before each step. If any step fails it yields an `error` event and stops; otherwise it finishes with a `done` event carrying the full profile record, extracted facts, and chunk count.

---

## Step-by-Step

### Step 1 — Parse Document

**File:** `agent/resume/parser.py`

Inspects the file extension and dispatches to the right parser:

| Extension | Library | How |
|---|---|---|
| `.pdf` | `pypdf` | `PdfReader` extracts text page by page |
| `.docx` / `.doc` | `python-docx` | Joins all paragraph text |
| other | — | Decoded as UTF-8 |

Returns a single plain-text string. If the result is empty, the pipeline emits an error and stops.

---

### Step 2 — Structured Extraction (GPT-4o)

**File:** `agent/resume/extractor.py` — `extract_profile(raw_text)`

Sends up to 24,000 characters of raw text to GPT-4o with a strict system prompt demanding a canonical JSON shape:

```json
{
  "contact":  { "name", "email", "phone", "location", "linkedin", "website" },
  "summary":  "...",
  "skills":   ["..."],
  "experience": [{ "company", "title", "location", "start", "end", "bullets": ["..."] }],
  "education":  [{ "institution", "degree", "field", "start", "end", "details" }],
  "certifications": ["..."],
  "projects":  [{ "name", "description", "bullets": ["..."] }]
}
```

Returns a Python dict. Empty strings / empty arrays are used where information is absent. GPT-4o is instructed never to invent data.

---

### Step 3 — Persist to SQLite

**File:** `db/sqlite.py` — `create_resume_profile(user_id, filename, profile)`

Writes the canonical profile JSON to the `resume_profiles` table with an auto-incremented `version` number. Returns the full DB record including `id` and `version`, which are used as metadata in subsequent steps.

**Table:** `resume_profiles`

| Column | Value |
|---|---|
| `user_id` | FK to `users` |
| `version` | auto-increment per user |
| `filename` | original upload filename |
| `profile_json` | canonical JSON string |
| `master_pdf_path` | filled in at Step 5 |

---

### Step 4 — Embed Resume Chunks

**File:** `agent/resume/embedder.py` — `embed_resume_chunks(user_id, raw_text, version)`

Splits the raw text into overlapping windows and upserts them into ChromaDB:

**Chunking:**
- Chunk size: ~2,048 characters (~512 tokens)
- Overlap: 200 characters — prevents a sentence straddling a boundary from being lost
- Example for a 5,000-char resume: chunks at `[0–2048]`, `[1848–3896]`, `[3696–5000]`

**ChromaDB write path (`db/chroma.py`):**

1. **Delete old vectors** — `delete_namespace(resume_ns(user_id))` drops the previous collection so re-uploads don't accumulate stale data
2. **Collection name** — `resume_ns(user_id)` → `"resume:{user_id}"` → stored as `"resume__{user_id}"` (ChromaDB disallows `:`)
3. **Collection settings:**
   - Embedding model: `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim, no API calls)
   - Distance metric: `hnsw:space = cosine` (cosine similarity, better for semantic text comparison)
4. **Upsert** — for each chunk, ChromaDB stores:

| Field | Value |
|---|---|
| `id` | random UUID |
| `document` | raw text chunk |
| `metadata` | `{"version": N, "chunk": i}` |
| *(vector)* | 384-dim float array produced internally by the embedding model |

**On-disk layout:**

```
.chroma/
  chroma.sqlite3          ← collection registry + metadata
  <uuid>/
    data_level0.bin       ← HNSW graph nodes
    header.bin
    length.bin
    link_lists.bin
```

One UUID folder per collection. `chroma.sqlite3` maps collection names to their UUID folders.

---

### Step 5 — Generate ATS Master PDF

**File:** `agent/resume/pdf_generator.py` — `generate_ats_pdf(profile, out_path)`

Renders the canonical profile through `agent/resume/templates/ats_resume.html` using Jinja2, then converts the HTML to PDF via WeasyPrint.

**ATS-safe rules enforced by the template:**
- Single column layout
- Arial / Helvetica 11pt
- 0.75-inch margins
- Standard section order: Contact → Summary → Skills → Experience → Education → Certifications → Projects
- No graphics, tables, columns, or headers/footers

Output path: `resumes/{user_id}_master_v{version}.pdf`  
The path is written back to the `resume_profiles.master_pdf_path` column.

> This step is **best-effort**. If WeasyPrint's native libraries (cairo/pango) are not installed, the pipeline emits a warning event and continues rather than failing.

---

### Step 6 — Extract & Embed Career Facts

**File:** `agent/resume/extractor.py` — `extract_facts(profile)` → `agent/resume/embedder.py` — `embed_career_facts(user_id, facts, version)`

A second GPT-4o call reads the structured profile and extracts **5–15 short, durable career-fact sentences**, for example:

- *"5 years of Python backend engineering experience"*
- *"Led a platform migration from monolith to microservices at Acme Corp"*
- *"Prefers remote-first companies in fintech or SaaS"*

These facts are upserted into ChromaDB namespace `memory:{user_id}` with `{"source": "resume", "version": N}` metadata. The agent queries this namespace at the start of every chat session to load relevant career context into the system prompt.

---

## Final SSE Event

```json
{
  "type": "done",
  "profile": { ...sqlite record... },
  "facts": ["fact 1", "fact 2", "..."],
  "chunks": 24
}
```

The Chainlit frontend listens for this event, updates the progress log, and renders example job-search prompt buttons so the user can immediately start searching.

---

## Data Written by the Pipeline

| Store | Namespace / Table | What is stored |
|---|---|---|
| SQLite `resume_profiles` | — | Canonical profile JSON + PDF path |
| ChromaDB | `resume__{user_id}` | Raw text chunks (for RAG retrieval) |
| ChromaDB | `memory__{user_id}` | Career fact sentences (for agent memory) |
| Filesystem | `resumes/` | ATS-safe master PDF |
