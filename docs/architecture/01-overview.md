# System Overview

Full map of every component and how they connect. Read top-to-bottom: user actions flow through the frontend into the API, the agent picks tools, tools hit external services and databases, everything is traced to LangSmith.

## Architecture Diagram

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#dbeafe', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#eff6ff'}}}%%
flowchart LR
    %% Custom Styling for Clean Layout
    classDef client fill:#f9fafd,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a;
    classDef gateway fill:#f0fdf4,stroke:#22c55e,stroke-width:2px,color:#14532d;
    classDef core fill:#faf5ff,stroke:#a855f7,stroke-width:2px,color:#581c87;
    classDef tools fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#7c2d12;
    classDef storage fill:#fef2f2,stroke:#ef4444,stroke-width:2px,color:#7f1d1d;
    classDef external fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#0f172a;
    classDef observability fill:#fff1f2,stroke:#f43f5e,stroke-width:2px,stroke-dasharray: 5 5,color:#881337;

    %% --- FRONTEND LAYER ---
    subgraph Frontend["Frontend Client (Gradio / HTML+JS)"]
        direction TB
        UI_ONBOARD["Onboarding Screen\n(Resume Upload)"]
        UI_CHAT["Chat Window\n(SSE EventSource)"]
        UI_HITL["HITL Card\n(Approve / Edit / Reject)"]
        UI_DASH["Dashboard\n(Tracker + Resume Versions)"]
    end
    class Frontend client;

    %% --- API GATEWAY LAYER ---
    subgraph API["FastAPI Backend Gateway\n(key endpoints — full list in 10-api.md)"]
        direction TB
        EP2["POST /resume/upload"]
        EP1["POST /chat/stream"]
        EP4["POST /chat/approve"]
        EP3["GET /applications"]
        EP5["POST /feedback"]
    end
    class API gateway;

    %% --- CORE ORCHESTRATION & STATE ---
    subgraph Engine["LangGraph Agent Engine"]
        direction TB
        GRAPH["ReAct StateGraph\n(plan → select → execute → synthesize)"]
        MEM["Memory Manager\n(memory.py)"]
    end
    class Engine core;

    %% --- AGENT CAPABILITY TOOLS ---
    subgraph Tools["Agent Toolbelt"]
        direction TB
        T_RAG["RAG Tool"]
        T_TAILOR["Resume Tailor Tool"]
        T_MCP["Filesystem MCP Tool"]
        T_SEARCH["Company Job Search"]
        T_RESEARCH["Company Research Tool"]
        T_APPLY["Auto-Apply Tool"]
    end
    class Tools tools;

    %% --- STORAGE & DATA ---
    subgraph Stores["Persistence & Context Layer"]
        direction TB
        SQLITE[("SQLite\n· Sessions\n· Episodic Memory\n· Applications\n· Resume Profiles")]
        CHROMA[("ChromaDB\n· Semantic Memory\n· RAG Chunks\n· Resume Embeddings")]
        RESUMES[("Local Disk\nresumes/\n  user_id/master/\n  user_id/tailored/")]
    end
    class Stores storage;

    %% --- EXTERNAL SERVICES ---
    subgraph External["External Services & APIs"]
        direction TB
        GPT["GPT-4o\n(OpenAI API)"]
        MCP_FS["Filesystem MCP\nServer"]
        DDG["DuckDuckGo Search\nAPI"]
        WEB["Public Web /\nCareers Pages"]
    end
    class External external;

    %% --- OBSERVABILITY SIDE TRACK ---
    subgraph Observability["Observability Track"]
        LANGSMITH["LangSmith\n(Tracing & Feedback)"]:::observability
    end

    %% --- CONNECTIVITY & INTERACTION FLOW ---

    %% Clients to Endpoints
    UI_ONBOARD -- "Multipart Upload" --> EP2
    UI_CHAT -- "SSE" --> EP1
    UI_HITL -- "Decision" --> EP4
    UI_DASH -- "Fetch Data" --> EP3

    %% Endpoints to Graph Logic
    EP2 & EP1 & EP4 --> GRAPH

    %% Engine Internal Flow
    GRAPH <--> MEM

    %% Memory Drivers to Databases
    MEM --> SQLITE
    MEM --> CHROMA

    %% Tool Bindings
    GRAPH --> Tools

    %% Tool Routing to Destinations
    T_RAG --> CHROMA

    T_TAILOR --> GPT
    T_TAILOR --> CHROMA
    T_TAILOR --> RESUMES

    T_MCP --> MCP_FS
    MCP_FS --> RESUMES

    T_SEARCH --> DDG
    T_SEARCH --> WEB
    T_SEARCH --> CHROMA

    T_RESEARCH --> DDG
    T_RESEARCH --> WEB

    T_APPLY --> WEB

    %% Core LLM Backing
    GRAPH -- "LLM Orchestration" --> GPT

    %% Diagnostics & Observability Paths
    GRAPH -. "Traces" .-> LANGSMITH
    EP5 -. "User Feedback" .-> LANGSMITH
```

## Layer Summary

| Layer | Components | Responsibility |
|-------|-----------|----------------|
| Frontend | Chat, Onboarding, Dashboard, HITL Card | User interaction, SSE rendering, HITL approval UI |
| FastAPI | 5 endpoint groups | REST + SSE gateway, request routing |
| LangGraph Agent | StateGraph, memory.py | ReAct reasoning loop, memory injection/storage |
| Agent Tools | 6 tools | Job search, resume tailoring, auto-apply, RAG, research, file I/O |
| Persistence | SQLite, ChromaDB | Structured data + vector embeddings |
| External | GPT-4o, DuckDuckGo, Web, MCP Server | LLM inference, web search, browser automation |
| Observability | LangSmith | End-to-end tracing, user feedback, evaluation datasets |

## Data Flow Summary

```
User message
  → POST /chat/stream
  → LangGraph: load memories → plan → tool_select → tool_execute (loop) → synthesize → respond
  → SSE token stream → browser
  → (if HITL needed) → pause → emit hitl_request → wait for POST /chat/approve → resume
  → post-turn: summarize → SQLite (episodic) + ChromaDB (semantic) + LangSmith (trace)
```
