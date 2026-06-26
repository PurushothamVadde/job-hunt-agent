"""SQLite persistence layer for JobHuntAI.

Contains all DDL plus CRUD helpers for every table:
users, sessions, messages, resume_profiles, episodic_memories, applications.

The module exposes synchronous helpers (SQLite calls are fast and local). When
called from async endpoints they are cheap enough to run inline, but a thin
``run_in_threadpool`` wrapper is available via :func:`a` for hot paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "jobhuntai.db")
DB_PATH = Path(DATABASE_URL)


# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    title TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resume_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    version INTEGER NOT NULL,
    filename TEXT NOT NULL,
    data_json TEXT NOT NULL,
    master_pdf_path TEXT,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS episodic_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    session_id TEXT REFERENCES sessions(session_id),
    summary TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    url TEXT,
    status TEXT DEFAULT 'applied',
    tailored_resume_path TEXT,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    next_action TEXT,
    next_action_date DATETIME
);
"""


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables. Idempotent. Call on application startup."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def create_user(username: str, email: str, hashed_password: str) -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, email, hashed_password) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username, email, hashed_password),
        )
    return get_user_by_id(user_id)


def get_user_by_username(username: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _row_to_dict(row)


def get_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return _row_to_dict(row)


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return _row_to_dict(row)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(user_id: str, title: Optional[str] = None) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, title or "New session"),
        )
    return get_session(session_id)


def get_session(session_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return _row_to_dict(row)


def list_sessions(user_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def touch_session(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET last_active = ? WHERE session_id = ?",
            (datetime.utcnow(), session_id),
        )


def update_session_title(session_id: str, title: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET title = ? WHERE session_id = ?",
            (title, session_id),
        )


def delete_session(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute(
            "DELETE FROM episodic_memories WHERE session_id = ?", (session_id,)
        )
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def add_message(session_id: str, role: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        return cur.lastrowid


def get_messages(session_id: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC"
    params: tuple = (session_id,)
    if limit:
        query = (
            "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC"
        )
        params = (session_id, limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Resume profiles
# --------------------------------------------------------------------------- #
def next_resume_version(user_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM resume_profiles "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["v"]) + 1


def create_resume_profile(
    user_id: str,
    filename: str,
    data: dict[str, Any],
    master_pdf_path: Optional[str] = None,
) -> dict[str, Any]:
    version = next_resume_version(user_id)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO resume_profiles "
            "(user_id, version, filename, data_json, master_pdf_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, version, filename, json.dumps(data), master_pdf_path),
        )
        rid = cur.lastrowid
    return get_resume_profile(rid)


def get_resume_profile(profile_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resume_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return _hydrate_resume(row)


def get_current_resume(user_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resume_profiles WHERE user_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return _hydrate_resume(row)


def list_resume_versions(user_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM resume_profiles WHERE user_id = ? ORDER BY version DESC",
            (user_id,),
        ).fetchall()
    return [_hydrate_resume(r) for r in rows]


def delete_resume_version(user_id: str, version_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM resume_profiles WHERE id = ? AND user_id = ?",
            (version_id, user_id),
        )
        return cur.rowcount > 0


def _hydrate_resume(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    try:
        d["data"] = json.loads(d.pop("data_json"))
    except (json.JSONDecodeError, KeyError):
        d["data"] = {}
    return d


# --------------------------------------------------------------------------- #
# Episodic memories
# --------------------------------------------------------------------------- #
def add_episodic_memory(user_id: str, session_id: Optional[str], summary: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO episodic_memories (user_id, session_id, summary) "
            "VALUES (?, ?, ?)",
            (user_id, session_id, summary),
        )
        return cur.lastrowid


def get_recent_episodic(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM episodic_memories WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #
def create_application(
    user_id: str,
    company: str,
    role: str,
    url: Optional[str] = None,
    status: str = "applied",
    tailored_resume_path: Optional[str] = None,
    notes: Optional[str] = None,
    next_action: Optional[str] = None,
    next_action_date: Optional[str] = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(user_id, company, role, url, status, tailored_resume_path, "
            "notes, next_action, next_action_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                company,
                role,
                url,
                status,
                tailored_resume_path,
                notes,
                next_action,
                next_action_date,
            ),
        )
        aid = cur.lastrowid
    return get_application(aid)


def get_application(app_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
    return _row_to_dict(row)


def list_applications(user_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY applied_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


_APPLICATION_FIELDS = {
    "company",
    "role",
    "url",
    "status",
    "tailored_resume_path",
    "notes",
    "next_action",
    "next_action_date",
}


def update_application(
    app_id: int, user_id: str, **fields: Any
) -> Optional[dict[str, Any]]:
    updates = {k: v for k, v in fields.items() if k in _APPLICATION_FIELDS}
    if not updates:
        return get_application(app_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [app_id, user_id]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE applications SET {set_clause} WHERE id = ? AND user_id = ?",
            params,
        )
    return get_application(app_id)


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH.resolve()}")
