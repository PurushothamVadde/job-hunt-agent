# Persistence

Two storage systems work together. SQLite captures structured, relational data (what happened). ChromaDB stores vector embeddings for semantic similarity search (what is true about the user).

## Two-Tier Memory

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#fce7f3', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#fdf2f8'}}}%%
flowchart LR
    subgraph WritePath["Write Path — end of each session"]
        W1["GPT-4o auto-summarizes conversation"]
        W2["GPT-4o extracts semantic facts"]
        W3[("SQLite — episodic_memories\nwhat happened this session")]
        W4[("ChromaDB — memory namespace\nwhat is true about the user")]
        W1 --> W3
        W2 --> W4
    end

    subgraph ReadPath["Read Path — start of each session"]
        R1["Incoming user message + user_id"]
        R2["SQLite: query last N episodic summaries"]
        R3["ChromaDB: top-K semantic similarity search"]
        R4["Merged context injected into system prompt"]
        R1 --> R2 & R3
        R2 --> R4
        R3 --> R4
    end

    subgraph Namespaces["ChromaDB Namespaces"]
        NS1["memory:{user_id}\nCareer facts extracted from chat"]
        NS2["resume:{user_id}\nChunked + embedded master resume"]
        NS3["docs:{user_id}\nUploaded career documents"]
    end

    W4 --- NS1
    R3 --- NS1 & NS2 & NS3
```

### Why Two Tiers?

| Tier | Store | What it holds | Query method |
|------|-------|---------------|--------------|
| Episodic | SQLite | "On 2025-06-10 user applied to Stripe, was rejected" | Row lookup by user_id, ordered by date |
| Semantic | ChromaDB | "User has 5 years Python", "Targets senior IC roles" | Cosine similarity against current message embedding |

Episodic memory gives the agent a timeline. Semantic memory gives it persistent facts that are relevant to whatever the user is asking right now.

---

## Database Schema

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#fce7f3', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#fdf2f8'}}}%%
erDiagram
    USERS {
        text user_id       PK  "UUID — primary tenant key"
        text email
        text name
        datetime created_at
    }

    SESSIONS {
        text session_id    PK  "UUID"
        text user_id       FK
        text title
        datetime created_at
        datetime last_active
    }

    MESSAGES {
        int  id            PK
        text session_id    FK
        text role              "user | assistant | tool"
        text content
        datetime created_at
    }

    RESUME_PROFILES {
        int  id            PK
        text user_id       FK
        int  version           "increments on each upload"
        text filename
        text data_json         "GPT-4o-extracted canonical JSON"
        text master_pdf_path
        datetime uploaded_at
    }

    EPISODIC_MEMORIES {
        int  id            PK
        text user_id       FK
        text session_id    FK
        text summary           "auto-generated at session end"
        datetime created_at
    }

    APPLICATIONS {
        int  id            PK
        text user_id       FK
        text company
        text role
        text url
        text status            "applied|phone_screen|interview|offer|rejected"
        text tailored_resume_path
        datetime applied_at
        text notes
        text next_action
        datetime next_action_date
    }

    USERS          ||--o{ SESSIONS          : "has"
    SESSIONS       ||--o{ MESSAGES          : "contains"
    USERS          ||--o{ RESUME_PROFILES   : "uploads"
    USERS          ||--o{ EPISODIC_MEMORIES : "accumulates"
    USERS          ||--o{ APPLICATIONS      : "tracks"
    SESSIONS       ||--o{ EPISODIC_MEMORIES : "generates"
```

## SQLite Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `users` | `user_id` (PK) | Top-level tenant; all other tables FK here |
| `sessions` | `session_id`, `title`, `last_active` | One row per conversation; agent can list/resume/delete |
| `messages` | `role`, `content` | Full message history per session |
| `resume_profiles` | `version`, `data_json`, `master_pdf_path` | New row on every upload; old versions never deleted |
| `episodic_memories` | `summary`, `created_at` | Written at end of each session by auto-summarize node |
| `applications` | `status`, `tailored_resume_path`, `next_action_date` | Job tracker; status progresses through the enum |

## ChromaDB Collections

| Namespace | Content | Populated by |
|-----------|---------|--------------|
| `resume:{user_id}` | Master resume chunks (512 tok, 50 tok overlap) | Resume ingestion pipeline |
| `memory:{user_id}` | Career facts extracted by GPT-4o | Post-turn memory write, resume ingestion |
| `docs:{user_id}` | Uploaded career documents (cover letters, interview notes) | `POST /documents/upload` |

## Embedding Model

All ChromaDB collections use `sentence-transformers/all-MiniLM-L6-v2` — runs locally, no API key required.

## Implementation Files

| File | Responsibility |
|------|---------------|
| `db/sqlite.py` | All SQLite DDL + CRUD helpers |
| `db/chroma.py` | ChromaDB client wrapper — upsert, query, delete by namespace |
| `agent/memory.py` | Orchestrates read (session start) and write (session end) across both stores |
