"""Application-tracker endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from db import sqlite

router = APIRouter(prefix="/applications", tags=["applications"])

VALID_STATUSES = {"applied", "phone_screen", "interview", "offer", "rejected"}


class ApplicationUpdate(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    url: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    next_action: Optional[str] = None
    next_action_date: Optional[str] = None
    tailored_resume_path: Optional[str] = None


@router.get("")
async def list_applications(user: dict[str, Any] = Depends(get_current_user)):
    return {"applications": sqlite.list_applications(user["user_id"])}


@router.patch("/{app_id}")
async def update_application(
    app_id: int,
    update: ApplicationUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    existing = sqlite.get_application(app_id)
    if not existing or existing["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Application not found")

    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(VALID_STATUSES)}",
        )
    updated = sqlite.update_application(app_id, user["user_id"], **fields)
    return updated
