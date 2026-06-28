"""SQLite-backed Chainlit data layer.

Enables the Chainlit thread sidebar by persisting threads and steps
to our existing SQLite database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional

import chainlit as cl
from chainlit.data import BaseDataLayer
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser

from db import sqlite

if TYPE_CHECKING:
    from chainlit.element import Element, ElementDict
    from chainlit.step import StepDict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteDataLayer(BaseDataLayer):
    """Minimal data layer wiring Chainlit's thread/step API to our SQLite DB."""

    # ── Users ────────────────────────────────────────────────────────────────

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        row = sqlite.get_user_by_id(identifier)
        if not row:
            return None
        return PersistedUser(
            id=row["user_id"],
            createdAt=str(row.get("created_at", "")),
            identifier=row["user_id"],
            display_name=row.get("display_name") or row.get("username"),
            metadata={},
        )

    async def create_user(self, user: cl.User) -> Optional[PersistedUser]:
        row = sqlite.get_user_by_id(user.identifier)
        if row:
            return await self.get_user(user.identifier)
        # Shouldn't normally be called (oauth_callback creates the user), but
        # handle gracefully just in case.
        return PersistedUser(
            id=user.identifier,
            createdAt=_now(),
            identifier=user.identifier,
            display_name=user.metadata.get("name"),
            metadata=user.metadata,
        )

    # ── Threads ──────────────────────────────────────────────────────────────

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        row = sqlite.get_session(thread_id)
        if not row:
            return None
        steps = self._load_steps(thread_id)
        return ThreadDict(
            id=thread_id,
            createdAt=str(row.get("created_at", "")),
            name=row.get("title") or "Chat",
            userId=row["user_id"],
            userIdentifier=row["user_id"],
            tags=[],
            metadata={},
            steps=steps,
            elements=[],
        )

    async def get_thread_author(self, thread_id: str) -> str:
        row = sqlite.get_session(thread_id)
        return row["user_id"] if row else ""

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        if name:
            sqlite.update_session_title(thread_id, name)

    async def delete_thread(self, thread_id: str) -> None:
        sqlite.delete_session(thread_id)

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        user_id = filters.userId if filters and filters.userId else None
        if not user_id:
            return PaginatedResponse(data=[], pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None))

        sessions = sqlite.list_sessions(user_id)

        # Apply search filter
        if filters and filters.search:
            q = filters.search.lower()
            sessions = [s for s in sessions if q in (s.get("title") or "").lower()]

        # Pagination
        cursor = pagination.cursor
        first = pagination.first or 20
        start = 0
        if cursor:
            ids = [s["session_id"] for s in sessions]
            if cursor in ids:
                start = ids.index(cursor) + 1
        page = sessions[start : start + first]
        has_next = (start + first) < len(sessions)
        end_cursor = page[-1]["session_id"] if page else None

        threads = []
        for s in page:
            steps = self._load_steps(s["session_id"])
            threads.append(
                ThreadDict(
                    id=s["session_id"],
                    createdAt=str(s.get("created_at", "")),
                    name=s.get("title") or "Chat",
                    userId=s["user_id"],
                    userIdentifier=s["user_id"],
                    tags=[],
                    metadata={},
                    steps=steps,
                    elements=[],
                )
            )

        start_cursor = page[0]["session_id"] if page else None
        return PaginatedResponse(
            data=threads,
            pageInfo=PageInfo(
                hasNextPage=has_next,
                startCursor=start_cursor,
                endCursor=end_cursor,
            ),
        )

    # ── Steps ────────────────────────────────────────────────────────────────

    async def create_step(self, step_dict: "StepDict") -> None:
        thread_id = step_dict.get("threadId")
        chainlit_type = step_dict.get("type", "")
        # Only handle user messages here — assistant messages come through update_step
        if chainlit_type != "user_message":
            return
        output = step_dict.get("output") or step_dict.get("input") or ""
        if not thread_id or not output:
            return
        if not sqlite.get_session(thread_id):
            return
        try:
            sqlite.add_message(thread_id, "user", output)
            session = sqlite.get_session(thread_id)
            if session and (not session.get("title") or session["title"] == "New session"):
                sqlite.update_session_title(thread_id, output[:60].strip())
        except Exception:
            pass

    async def update_step(self, step_dict: "StepDict") -> None:
        thread_id = step_dict.get("threadId")
        chainlit_type = step_dict.get("type", "assistant_message")
        output = step_dict.get("output") or ""
        # Only persist final assistant responses — ignore tool steps / empty updates
        if chainlit_type != "assistant_message" or not thread_id or not output:
            return
        if not sqlite.get_session(thread_id):
            return
        try:
            sqlite.upsert_assistant_step(thread_id, step_dict.get("id") or "", output)
        except Exception:
            pass

    async def delete_step(self, step_id: str) -> None:
        pass

    # ── Elements ─────────────────────────────────────────────────────────────

    async def get_element(
        self, thread_id: str, element_id: str
    ) -> Optional["ElementDict"]:
        return None

    async def create_element(self, element: "Element") -> None:
        pass

    async def delete_element(self, element_id: str, thread_id: Optional[str] = None) -> None:
        pass

    # ── Feedback ─────────────────────────────────────────────────────────────

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return feedback.id or ""

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    # ── Favorites ────────────────────────────────────────────────────────────

    async def get_favorite_steps(self, user_id: str) -> List["StepDict"]:
        return []

    async def set_step_favorite(self, step_dict: "StepDict", favorite: bool) -> "StepDict":
        return step_dict

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _load_steps(self, session_id: str) -> List["StepDict"]:
        messages = sqlite.get_messages(session_id)
        steps = []
        for m in messages:
            role = m.get("role", "assistant")
            steps.append(
                {
                    "id": m.get("step_uuid") or str(m["id"]),
                    "threadId": session_id,
                    "type": "user_message" if role == "user" else "assistant_message",
                    "output": m["content"],
                    "createdAt": str(m.get("created_at", "")),
                    "name": "User" if role == "user" else "Assistant",
                    "input": "",
                    "isError": False,
                    "metadata": {},
                    "tags": [],
                }
            )
        return steps

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass
