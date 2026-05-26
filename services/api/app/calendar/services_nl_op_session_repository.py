"""Schema bootstrap for ``services_nl_op_sessions`` (Epic 13, story 13.01).

The session state-machine + repository class itself lands in story 13.04;
this module creates the table so 13.04's repo has a place to land. Lives in
``.data/semantaix_nl_ops.db`` (alongside ``admin_nl_op_sessions``), NOT in
``semantaix_calendar.db`` — per the L2 validation fix in the story spec.

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``
mean second-run is a no-op, suitable for invocation on every container boot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_services_nl_ops_schema(db_path: str) -> None:
    """Create ``services_nl_op_sessions`` + indexes if absent.

    Schema mirrors ``admin_nl_op_sessions`` (Epic 10.05) but is **operator- +
    project-scoped** so cross-operator replays can be rejected. The full
    semantics (TTL, confirm_token, one-pending-per-(project, operator),
    soft-delete with 30-day retention) land in story 13.04's repository class.
    """
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS services_nl_op_sessions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                originating_operator TEXT NOT NULL,
                op_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                preview TEXT NOT NULL,
                confirm_token_sha256 TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                soft_deleted_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS services_nl_op_sessions_lookup_idx "
            "ON services_nl_op_sessions(project_id, originating_operator, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS services_nl_op_sessions_expiry_idx "
            "ON services_nl_op_sessions(status, expires_at)"
        )
