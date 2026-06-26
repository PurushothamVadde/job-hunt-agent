"""Resume endpoints: upload (SSE ingestion), current, versions, delete."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from agent.resume import ingestion
from api.auth import get_current_user

router = APIRouter(prefix="/resume", tags=["resume"])


def _sse(event: dict[str, Any]) -> dict[str, str]:
    return {"data": json.dumps(event, default=str)}


@router.post("/upload")
async def upload_resume(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
):
    """Stream the six-step ingestion pipeline as SSE progress events."""
    raw = await file.read()
    filename = file.filename or "resume.pdf"
    user_id = user["user_id"]

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        async for ev in ingestion.ingest(user_id, filename, raw):
            yield _sse(ev)

    return EventSourceResponse(event_stream())


@router.get("/current")
async def current_resume(user: dict[str, Any] = Depends(get_current_user)):
    from db import sqlite

    profile = sqlite.get_current_resume(user["user_id"])
    if not profile:
        raise HTTPException(status_code=404, detail="No resume uploaded yet")
    return profile


@router.get("/versions")
async def resume_versions(user: dict[str, Any] = Depends(get_current_user)):
    from db import sqlite

    return {"versions": sqlite.list_resume_versions(user["user_id"])}


@router.delete("/versions/{version_id}")
async def delete_resume_version(
    version_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    from db import sqlite

    ok = sqlite.delete_resume_version(user["user_id"], version_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"deleted": version_id}
