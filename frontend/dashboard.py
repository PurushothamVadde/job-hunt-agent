"""Dashboard helpers: application tracker + resume versions via the API."""

from __future__ import annotations

from typing import Any

import httpx

from frontend import API_BASE


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fetch_applications(token: str) -> list[list[Any]]:
    """Return a table-friendly list of rows for the application tracker."""
    if not token:
        return []
    try:
        resp = httpx.get(
            f"{API_BASE}/applications", headers=_headers(token), timeout=30.0
        )
        resp.raise_for_status()
        apps = resp.json().get("applications", [])
    except Exception:
        return []
    return [
        [
            a.get("id"),
            a.get("company"),
            a.get("role"),
            a.get("status"),
            a.get("applied_at"),
            a.get("next_action") or "",
            a.get("next_action_date") or "",
        ]
        for a in apps
    ]


APP_COLUMNS = [
    "ID",
    "Company",
    "Role",
    "Status",
    "Applied",
    "Next action",
    "Next date",
]


def update_application_status(token: str, app_id: int, status: str) -> str:
    if not token or not app_id:
        return "Need a token and application id."
    try:
        resp = httpx.patch(
            f"{API_BASE}/applications/{int(app_id)}",
            headers=_headers(token),
            json={"status": status},
            timeout=30.0,
        )
    except Exception as exc:
        return f"Error: {exc}"
    if resp.status_code == 200:
        return f"Updated application {app_id} -> {status}"
    return f"Update failed: {resp.text}"


def fetch_resume_versions(token: str) -> list[list[Any]]:
    if not token:
        return []
    try:
        resp = httpx.get(
            f"{API_BASE}/resume/versions", headers=_headers(token), timeout=30.0
        )
        resp.raise_for_status()
        versions = resp.json().get("versions", [])
    except Exception:
        return []
    return [
        [
            v.get("id"),
            v.get("version"),
            v.get("filename"),
            v.get("uploaded_at"),
            v.get("master_pdf_path") or "",
        ]
        for v in versions
    ]


VERSION_COLUMNS = ["ID", "Version", "Filename", "Uploaded", "Master PDF"]
