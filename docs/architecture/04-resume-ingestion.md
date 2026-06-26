# Resume Ingestion Pipeline

Triggered by `POST /resume/upload`. Runs automatically after a PDF or DOCX is uploaded. Progress is streamed to the browser over SSE after each step. The chat interface is locked until this pipeline completes successfully.

## Sequence Diagram

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#d1fae5', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#ecfdf5'}}}%%
sequenceDiagram
    actor User
    participant FE  as Frontend
    participant API as FastAPI
    participant LLM as GPT-4o
    participant SQL as SQLite
    participant VDB as ChromaDB
    participant PDF as WeasyPrint

    User->>FE: Drop PDF / DOCX (max 5 MB)
    FE->>API: POST /resume/upload (multipart)
    API-->>FE: SSE stream opened

    Note over API: Step 1 — Parse raw text
    API->>API: pypdf (PDF) or python-docx (DOCX) → plain text

    Note over API,LLM: Step 2 — Structured extraction
    API->>LLM: Extract canonical JSON from resume text
    LLM-->>API: Structured JSON (contact, skills, experience, education, projects)

    Note over API,SQL: Step 3 — Persist profile
    API->>SQL: INSERT resume_profiles (user_id, version, data_json, uploaded_at)
    API-->>FE: SSE progress — "profile saved"

    Note over API,VDB: Step 4 — Index embeddings
    API->>VDB: Chunk text (512 tokens, 50-token overlap) + embed
    VDB-->>API: Stored in namespace resume:{user_id}
    API-->>FE: SSE progress — "embeddings indexed"

    Note over API,LLM: Step 5 — Extract semantic facts
    API->>LLM: Extract long-term career facts from structured data
    LLM-->>API: ["has 5 years Python", "located in SF", "targets senior IC"]
    API->>VDB: Store facts in namespace memory:{user_id}

    Note over API,PDF: Step 6 — Regenerate ATS master PDF
    API->>PDF: Render Jinja2 ATS template with structured data
    PDF-->>API: master_resume.pdf (single-column, ATS-safe)
    API->>SQL: UPDATE resume_profiles SET master_pdf_path
    API-->>FE: SSE event — resume_ready

    FE->>User: Chat interface unlocked
```

## Extraction Schema

GPT-4o extracts the resume into this canonical JSON structure:

```json
{
  "contact": {
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin": "",
    "github": ""
  },
  "summary": "",
  "skills": [""],
  "experience": [
    { "company": "", "title": "", "dates": "", "bullets": [""] }
  ],
  "education": [
    { "institution": "", "degree": "", "field": "", "year": "" }
  ],
  "certifications": [""],
  "projects": [
    { "name": "", "description": "", "tech_stack": [""] }
  ]
}
```

## Chunking Strategy

| Parameter | Value |
|-----------|-------|
| Chunk size | 512 tokens |
| Overlap | 50 tokens |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` (local) |
| ChromaDB namespace | `resume:{user_id}` |

## Versioning

Each upload creates a new row in `resume_profiles` with an incremented `version` number. Previous versions are never deleted — they remain queryable via `GET /resume/versions`. The master PDF for each version is stored at:

```
resumes/{user_id}/master/master_resume_v{version}.pdf
```

## Onboarding Guard

Until step 6 completes, any chat message returns:

```json
{ "type": "onboarding_required", "message": "Please upload your master resume to get started." }
```

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /resume/upload` | Trigger the ingestion pipeline (multipart, field: `resume_file`) |
| `GET /resume/current` | Return the latest version's structured JSON + PDF path |
| `GET /resume/versions` | List all versions with metadata |
| `DELETE /resume/versions/{version_id}` | Soft-delete a specific version |

## Implementation Files

| File | Responsibility |
|------|---------------|
| `api/resume.py` | Upload endpoint, SSE progress events |
| `agent/resume/ingestion.py` | Parse → extract → chunk → embed pipeline |
| `agent/resume/pdf_generator.py` | Jinja2 render + WeasyPrint conversion |
| `agent/resume/templates/ats_resume.html` | ATS-safe single-column HTML template |
