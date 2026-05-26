"""Per-(project, operator) dedup for the ``/calendar_service`` migration-hint DM
(Epic 13, story 13.03).

The ``/calendar_service`` slash command is being retired in favor of ``/service``.
On every invocation the bot emits a structured deprecation log; on the **first**
invocation per ``(project_id, operator)`` it also DMs the operator a one-line
migration hint. To survive bot restarts the "already sent" mark is persisted
here.

Lives in ``.data/semantaix_nl_ops.db`` (NOT in ``semantaix_calendar.db``) — it
is a behavior-attached dedup table, similar in shape to the other NL-ops
support state, and keeping it here keeps the calendar DB schema stable. The
story spec calls this placement out explicitly.

Sync ``sqlite3`` per project-context; callers dispatch via ``asyncio.to_thread``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_calendar_service_alias_hint_schema(db_path: str) -> None:
    """Create the ``calendar_service_alias_hint_sent`` table if absent.

    Idempotent — second-run is a no-op, safe to call on every container boot.
    """
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_service_alias_hint_sent (
                project_id INTEGER NOT NULL,
                operator TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (project_id, operator)
            )
            """
        )


def should_send_calendar_service_alias_hint(
    *,
    db_path: str,
    project_id: int,
    operator: str,
    now: datetime,
) -> bool:
    """Return True if no prior hint was sent for this (project, operator); insert.

    Atomic: ``INSERT OR IGNORE`` is used so concurrent callers cannot both
    receive True for the same key. ``cursor.rowcount`` is 1 when the row was
    newly inserted (caller should DM), 0 when the row already existed (caller
    should skip).
    """
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO calendar_service_alias_hint_sent
                (project_id, operator, sent_at)
            VALUES (?, ?, ?)
            """,
            (project_id, operator, now.isoformat()),
        )
        return cursor.rowcount == 1
