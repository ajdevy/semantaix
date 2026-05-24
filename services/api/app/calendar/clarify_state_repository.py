"""One-turn clarify state for the availability answerer (Epic 11, story 11.07).

FR-22 requires the bot to ask exactly **once** when a customer's availability
request is ambiguous (no service named, an unknown service, two matching
services, or a missing/unparseable time) before escalating to a human. This
repository records the lightweight "I already asked once" flag scoped to a
conversation (``chat_id``), so the *next* still-unresolved inbound escalates
instead of looping clarifications forever.

The state is intentionally minimal — a single per-chat row holding the trace_id
that armed it. It is **consumed** (cleared) the moment the answerer reads it, so
a resolved follow-up, a different intent, or a successful answer all naturally
leave no stale flag behind. Sync ``sqlite3`` per project-context; callers
dispatch via ``asyncio.to_thread``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CalendarClarifyStateRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_clarify_state (
                    chat_id INTEGER PRIMARY KEY,
                    trace_id TEXT,
                    armed_at TEXT
                )
                """
            )

    def is_armed(self, chat_id: int) -> bool:
        """True iff a clarifying question was already asked for this chat."""
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM calendar_clarify_state WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return row is not None

    def arm(self, chat_id: int, *, trace_id: str) -> None:
        """Record that the one clarifying question has now been asked."""
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_clarify_state (chat_id, trace_id, armed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    trace_id = excluded.trace_id,
                    armed_at = excluded.armed_at
                """,
                (chat_id, trace_id, _now()),
            )

    def clear(self, chat_id: int) -> None:
        """Drop any clarify flag for this chat (resolved / answered / off-intent)."""
        with _connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM calendar_clarify_state WHERE chat_id = ?",
                (chat_id,),
            )
