"""LangSmith observability helpers.

Every public function is a no-op unless ``LANGCHAIN_TRACING_V2 == "true"`` and an
API key is present, so the rest of the application can call these freely without
worrying about whether tracing is configured.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterator, Optional

from dotenv import load_dotenv

load_dotenv()


def tracing_enabled() -> bool:
    return (
        os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
        and bool(os.getenv("LANGCHAIN_API_KEY"))
    )


@lru_cache(maxsize=1)
def get_client():
    """Return a cached LangSmith ``Client`` or ``None`` when tracing is off."""
    if not tracing_enabled():
        return None
    try:
        from langsmith import Client

        return Client(
            api_url=os.getenv(
                "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
            ),
            api_key=os.getenv("LANGCHAIN_API_KEY"),
        )
    except Exception:
        return None


def project_name() -> str:
    return os.getenv("LANGCHAIN_PROJECT", "jobhuntai")


def create_run(
    name: str,
    inputs: dict[str, Any],
    run_type: str = "chain",
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Create a top-level run; returns its id (or ``None`` if tracing is off)."""
    client = get_client()
    if client is None:
        return None
    run_id = str(uuid.uuid4())
    try:
        client.create_run(
            id=run_id,
            name=name,
            run_type=run_type,
            inputs=inputs,
            project_name=project_name(),
            extra={"metadata": metadata or {}},
            start_time=datetime.now(timezone.utc),
        )
        return run_id
    except Exception:
        return None


def end_run(
    run_id: Optional[str],
    outputs: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    client = get_client()
    if client is None or run_id is None:
        return
    try:
        client.update_run(
            run_id,
            outputs=outputs or {},
            error=error,
            end_time=datetime.now(timezone.utc),
        )
    except Exception:
        pass


def log_feedback(
    run_id: Optional[str],
    key: str,
    score: float,
    comment: Optional[str] = None,
) -> None:
    client = get_client()
    if client is None or run_id is None:
        return
    try:
        client.create_feedback(run_id, key=key, score=score, comment=comment)
    except Exception:
        pass


def flush_trace() -> None:
    """Best-effort flush of buffered traces at end of a turn."""
    client = get_client()
    if client is None:
        return
    try:
        flush = getattr(client, "flush", None)
        if callable(flush):
            flush()
    except Exception:
        pass


@contextmanager
def trace(name: str, inputs: dict[str, Any], **metadata: Any) -> Iterator[Optional[str]]:
    """Context manager wrapping :func:`create_run` / :func:`end_run`."""
    run_id = create_run(name, inputs, metadata=metadata)
    try:
        yield run_id
        end_run(run_id, outputs={"status": "ok"})
    except Exception as exc:  # pragma: no cover - defensive
        end_run(run_id, error=str(exc))
        raise


def list_recent_traces(limit: int = 20) -> list[dict[str, Any]]:
    """Used by the ``/admin/traces`` endpoint. Returns lightweight dicts."""
    client = get_client()
    if client is None:
        return []
    try:
        runs = client.list_runs(project_name=project_name(), limit=limit)
        out = []
        for r in runs:
            out.append(
                {
                    "id": str(getattr(r, "id", "")),
                    "name": getattr(r, "name", ""),
                    "run_type": getattr(r, "run_type", ""),
                    "start_time": str(getattr(r, "start_time", "")),
                    "error": getattr(r, "error", None),
                }
            )
        return out
    except Exception:
        return []
