"""Operator multi-message KB-upload session state.

When the operator opens KB intent (slash, free-text phrase, or caption on
an attached file) we persist a short-lived session keyed by
`(chat_id, username)`. Subsequent attachment-only Telegram updates from
the same operator in the same chat are then routed into KB upload via
`_process_telegram_update` instead of being silently dropped by the
attachment-only guard.

State lives in `semantaix_hitl.db` next to `hitl_runtime_config` so the
bot keeps a single SQLite file for ephemeral runtime state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> datetime:
    return datetime.now(UTC)


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_kb_session (
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                is_confidential INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, username)
            )
            """
        )


@dataclass(frozen=True)
class OperatorKbSession:
    chat_id: int
    username: str
    is_confidential: bool
    created_at: str
    expires_at: str


class OperatorKbSessionRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def upsert(
        self,
        *,
        chat_id: int,
        username: str,
        is_confidential: bool,
        ttl_seconds: int,
    ) -> OperatorKbSession:
        now = _now()
        expires = now + timedelta(seconds=ttl_seconds)
        created_at = now.isoformat()
        expires_at = expires.isoformat()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO operator_kb_session
                    (chat_id, username, is_confidential, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, username) DO UPDATE SET
                    is_confidential = excluded.is_confidential,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (chat_id, username, 1 if is_confidential else 0, created_at, expires_at),
            )
        return OperatorKbSession(
            chat_id=chat_id,
            username=username,
            is_confidential=is_confidential,
            created_at=created_at,
            expires_at=expires_at,
        )

    def get_active(self, *, chat_id: int, username: str) -> OperatorKbSession | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT chat_id, username, is_confidential, created_at, expires_at
                FROM operator_kb_session
                WHERE chat_id = ? AND username = ?
                """,
                (chat_id, username),
            ).fetchone()
        if row is None:
            return None
        expires = datetime.fromisoformat(str(row["expires_at"]))
        if expires <= _now():
            return None
        return OperatorKbSession(
            chat_id=int(row["chat_id"]),
            username=str(row["username"]),
            is_confidential=bool(row["is_confidential"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )

    def clear(self, *, chat_id: int, username: str) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM operator_kb_session WHERE chat_id = ? AND username = ?",
                (chat_id, username),
            )
