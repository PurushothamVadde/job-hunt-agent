"""Onboarding helpers: login / register + resume upload via the API."""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from frontend import API_BASE


def login(username: str, password: str) -> tuple[Optional[str], str]:
    """Return ``(token, message)``."""
    try:
        resp = httpx.post(
            f"{API_BASE}/auth/login",
            json={"username": username, "password": password},
            timeout=30.0,
        )
    except Exception as exc:
        return None, f"Connection error: {exc}"
    if resp.status_code == 200:
        data = resp.json()
        return data["access_token"], f"Logged in as {data['username']}"
    return None, f"Login failed: {resp.text}"


def register(
    username: str, email: str, password: str
) -> tuple[Optional[str], str]:
    try:
        resp = httpx.post(
            f"{API_BASE}/auth/register",
            json={"username": username, "email": email, "password": password},
            timeout=30.0,
        )
    except Exception as exc:
        return None, f"Connection error: {exc}"
    if resp.status_code == 200:
        data = resp.json()
        return data["access_token"], f"Registered and logged in as {data['username']}"
    return None, f"Registration failed: {resp.text}"


def upload_resume(token: str, file_path: str) -> str:
    """Stream the ingestion SSE and return a concatenated progress log."""
    if not token:
        return "Please log in first."
    if not file_path:
        return "No file selected."

    headers = {"Authorization": f"Bearer {token}"}
    log_lines: list[str] = []
    try:
        with open(file_path, "rb") as fh:
            files = {"file": (file_path.split("/")[-1], fh, "application/octet-stream")}
            with httpx.stream(
                "POST",
                f"{API_BASE}/resume/upload",
                headers=headers,
                files=files,
                timeout=300.0,
            ) as resp:
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = json.loads(line[len("data:"):].strip())
                    log_lines.append(_format_event(payload))
    except Exception as exc:
        return "\n".join(log_lines + [f"Error: {exc}"])
    return "\n".join(log_lines)


def _format_event(event: dict[str, Any]) -> str:
    etype = event.get("type")
    if etype == "progress":
        return f"... {event.get('step')}"
    if etype == "done":
        return (
            f"Done. Indexed {event.get('chunks', 0)} chunks, "
            f"{len(event.get('facts', []))} facts extracted."
        )
    if etype == "error":
        return f"Error: {event.get('message')}"
    return json.dumps(event)
