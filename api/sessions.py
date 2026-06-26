"""Session management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from db import sqlite

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(user: dict[str, Any] = Depends(get_current_user)):
    return {"sessions": sqlite.list_sessions(user["user_id"])}


@router.get("/{session_id}/messages")
async def session_messages(
    session_id: str, user: dict[str, Any] = Depends(get_current_user)
):
    session = sqlite.get_session(session_id)
    if not session or session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": sqlite.get_messages(session_id)}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str, user: dict[str, Any] = Depends(get_current_user)
):
    session = sqlite.get_session(session_id)
    if not session or session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Session not found")
    sqlite.delete_session(session_id)
    return {"deleted": session_id}
