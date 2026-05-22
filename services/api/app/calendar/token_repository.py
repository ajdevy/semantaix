"""Per-operator Google refresh tokens, encrypted at rest (Epic 11, story 11.01).

A leaked refresh token grants standing access to an operator's calendar, so
tokens are stored encrypted with an injected ``cryptography.fernet.Fernet``
whose key comes from ``Settings`` (env) — never read from the DB, never logged.
The stored ``refresh_token_encrypted`` BLOB is ciphertext; only ``Fernet``
roundtrips it. Sync ``sqlite3``; callers dispatch via ``asyncio.to_thread``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

STATUS_CONNECTED = "connected"
STATUS_RECONNECT_NEEDED = "reconnect_needed"


class TokenNotFound(Exception):
    """Raised when no refresh token exists for a (project_id, operator)."""


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_token_schema(db_path: str) -> None:
    """Idempotently create ``calendar_operator_tokens`` without a Fernet key.

    Lets api startup bootstrap the table even when no encryption key is
    configured yet; crypto operations still require an injected ``Fernet``.
    """
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_operator_tokens (
                project_id INTEGER,
                operator TEXT,
                refresh_token_encrypted BLOB NOT NULL,
                status TEXT NOT NULL DEFAULT 'connected',
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (project_id, operator)
            )
            """
        )


class CalendarTokenRepository:
    def __init__(self, *, db_path: str, fernet: Fernet) -> None:
        self.db_path = db_path
        self._fernet = fernet
        self.init_schema()

    def init_schema(self) -> None:
        init_token_schema(self.db_path)

    def upsert(self, project_id: int, operator: str, refresh_token: str) -> None:
        now = _now()
        encrypted = self._fernet.encrypt(refresh_token.encode("utf-8"))
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_operator_tokens
                    (project_id, operator, refresh_token_encrypted,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, operator) DO UPDATE SET
                    refresh_token_encrypted = excluded.refresh_token_encrypted,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (project_id, operator, encrypted, STATUS_CONNECTED, now, now),
            )

    def get_refresh_token(self, project_id: int, operator: str) -> str:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT refresh_token_encrypted
                FROM calendar_operator_tokens
                WHERE project_id = ? AND operator = ?
                """,
                (project_id, operator),
            ).fetchone()
        if row is None:
            raise TokenNotFound("token_not_found")
        return self._fernet.decrypt(row["refresh_token_encrypted"]).decode("utf-8")

    def set_status(self, project_id: int, operator: str, status: str) -> None:
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE calendar_operator_tokens
                SET status = ?, updated_at = ?
                WHERE project_id = ? AND operator = ?
                """,
                (status, now, project_id, operator),
            )

    def delete(self, project_id: int, operator: str) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                DELETE FROM calendar_operator_tokens
                WHERE project_id = ? AND operator = ?
                """,
                (project_id, operator),
            )
