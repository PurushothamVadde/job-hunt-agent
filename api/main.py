"""FastAPI application entry point for JobHuntAI.

Creates SQLite tables on startup, mounts all routers, enables CORS for local
dev, and exposes a ``/admin/traces`` observability endpoint.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import applications, auth, chat, documents, resume, sessions
from db import sqlite
from observability import langsmith

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create all tables.
    sqlite.init_db()
    yield
    # Shutdown: flush any buffered traces.
    langsmith.flush_trace()


app = FastAPI(title="JobHuntAI", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(resume.router)
app.include_router(sessions.router)
app.include_router(documents.router)
app.include_router(applications.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin/traces")
async def admin_traces(limit: int = 20) -> dict[str, list]:
    return {"traces": langsmith.list_recent_traces(limit)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
