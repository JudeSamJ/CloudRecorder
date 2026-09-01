"""Local session-tracking store for the Phase 6 desktop companion.

SQLite (stdlib sqlite3), not a flat file or in-memory-only state: the companion
needs atomic per-session stage transitions that are queryable by stage ("every
session AWAITING_RECONSTRUCTION") and that survive an app restart without special
recovery logic — the DB *is* the recovery state, so "closed mid-upload, relaunched
later" is not a special case, just a resumed poll loop over the same rows. This is
a single local file (companion_state.db, gitignored), not a server — no new
infrastructure, just the stdlib.

Every method opens its own short-lived connection rather than sharing one across
threads. This app touches the store from three different threads (the tray icon's
callback thread, the orchestrator's polling thread, and Tkinter's main thread for
the dashboard) — per-call connections sidestep sqlite3's cross-thread connection
restrictions entirely rather than requiring locks or check_same_thread=False.
"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "companion_state.db"

# Stage values, in pipeline order. FAILED_* are terminal until a manual retry
# resets them back to the preceding AWAITING_* stage.
AWAITING_RECONSTRUCTION = "AWAITING_RECONSTRUCTION"
RECONSTRUCTING = "RECONSTRUCTING"
AWAITING_PROXY = "AWAITING_PROXY"
GENERATING_PROXY = "GENERATING_PROXY"
READY = "READY"
FAILED_RECONSTRUCTION = "FAILED_RECONSTRUCTION"
FAILED_PROXY = "FAILED_PROXY"


@dataclass
class SessionRecord:
    session_id: str
    project_name: str
    chunk_count: int
    total_bytes: int
    marker_file_id: str
    stage: str
    paused: bool
    error_message: str | None
    master_file_id: str | None
    proxy_file_id: str | None
    local_proxy_path: str | None
    created_at_ms: int
    updated_at_ms: int


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                total_bytes INTEGER NOT NULL,
                marker_file_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                paused INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                master_file_id TEXT,
                proxy_file_id TEXT,
                local_proxy_path TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )


def known_session_ids() -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT session_id FROM sessions").fetchall()
        return {row["session_id"] for row in rows}


def add_new_session(session_id: str, project_name: str, chunk_count: int, total_bytes: int, marker_file_id: str) -> None:
    """Inserts a newly-discovered completed session at AWAITING_RECONSTRUCTION.
    No-ops (INSERT OR IGNORE) if already known, so re-scanning Drive markers is safe."""
    now = _now_ms()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions
                (session_id, project_name, chunk_count, total_bytes, marker_file_id,
                 stage, paused, created_at_ms, updated_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (session_id, project_name, chunk_count, total_bytes, marker_file_id,
             AWAITING_RECONSTRUCTION, now, now),
        )


def get_session(session_id: str) -> SessionRecord | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return _to_record(row) if row else None


def list_sessions() -> list[SessionRecord]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at_ms DESC").fetchall()
        return [_to_record(row) for row in rows]


def list_by_stage(stage: str, include_paused: bool = False) -> list[SessionRecord]:
    with _connect() as conn:
        query = "SELECT * FROM sessions WHERE stage = ?"
        params: list = [stage]
        if not include_paused:
            query += " AND paused = 0"
        rows = conn.execute(query, params).fetchall()
        return [_to_record(row) for row in rows]


def set_stage(session_id: str, stage: str, error_message: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET stage = ?, error_message = ?, updated_at_ms = ? WHERE session_id = ?",
            (stage, error_message, _now_ms(), session_id),
        )


def set_master_result(session_id: str, master_file_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET master_file_id = ?, stage = ?, error_message = NULL, updated_at_ms = ? "
            "WHERE session_id = ?",
            (master_file_id, AWAITING_PROXY, _now_ms(), session_id),
        )


def set_proxy_result(session_id: str, proxy_file_id: str, local_proxy_path: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET proxy_file_id = ?, local_proxy_path = ?, stage = ?, error_message = NULL, "
            "updated_at_ms = ? WHERE session_id = ?",
            (proxy_file_id, local_proxy_path, READY, _now_ms(), session_id),
        )


def set_paused(session_id: str, paused: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET paused = ?, updated_at_ms = ? WHERE session_id = ?",
            (1 if paused else 0, _now_ms(), session_id),
        )


def retry_failed(session_id: str) -> None:
    """Resets a FAILED_* session back to the AWAITING_* stage that precedes it,
    clearing the error, so the next poll picks it up again. No-op for any other stage."""
    session = get_session(session_id)
    if session is None:
        return
    if session.stage == FAILED_RECONSTRUCTION:
        set_stage(session_id, AWAITING_RECONSTRUCTION)
    elif session.stage == FAILED_PROXY:
        set_stage(session_id, AWAITING_PROXY)


def _to_record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        project_name=row["project_name"],
        chunk_count=row["chunk_count"],
        total_bytes=row["total_bytes"],
        marker_file_id=row["marker_file_id"],
        stage=row["stage"],
        paused=bool(row["paused"]),
        error_message=row["error_message"],
        master_file_id=row["master_file_id"],
        proxy_file_id=row["proxy_file_id"],
        local_proxy_path=row["local_proxy_path"],
        created_at_ms=row["created_at_ms"],
        updated_at_ms=row["updated_at_ms"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)
