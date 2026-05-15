"""Web admin auth — one-time Telegram codes + permanent session cookies.

Codes are 6-digit numeric, single-use, 5-minute TTL, capped at 5 wrong attempts.
Sessions have no expiry — `/logout` (revoke) is the only way out.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_CODE_TTL_SECONDS = 5 * 60
_MAX_FAILED_ATTEMPTS = 5
_SESSION_ID_BYTES = 32


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_auth_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                code TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_auth_codes_username_active
                ON web_auth_codes (username, consumed_at, expires_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                session_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_sessions_username
                ON web_sessions (username)
            """
        )


@dataclass(frozen=True)
class WebSession:
    session_id: str
    username: str
    role: str
    created_at: str
    last_seen_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class CodeVerification:
    ok: bool
    reason: str | None
    remaining_attempts: int
    chat_id: int | None


class WebAuthRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def create_code(self, *, username: str, chat_id: int) -> str:
        now = datetime.now(UTC)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = (now + timedelta(seconds=_CODE_TTL_SECONDS)).isoformat()
        with _connect(self.db_path) as connection:
            # Supersede any prior unconsumed code for this username.
            connection.execute(
                """
                UPDATE web_auth_codes
                SET consumed_at = ?
                WHERE username = ? AND consumed_at IS NULL
                """,
                (now.isoformat(), username),
            )
            connection.execute(
                """
                INSERT INTO web_auth_codes
                    (username, code, chat_id, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, code, chat_id, expires_at, now.isoformat()),
            )
        return code

    def consume_code(self, *, username: str, code: str) -> CodeVerification:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, code, chat_id, expires_at, consumed_at, failed_attempts
                FROM web_auth_codes
                WHERE username = ? AND consumed_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (username,),
            ).fetchone()
            if row is None:
                return CodeVerification(
                    ok=False, reason="invalid", remaining_attempts=0, chat_id=None
                )
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            if expires_at <= now:
                connection.execute(
                    "UPDATE web_auth_codes SET consumed_at = ? WHERE id = ?",
                    (now_iso, int(row["id"])),
                )
                return CodeVerification(
                    ok=False, reason="expired", remaining_attempts=0, chat_id=None
                )
            failed = int(row["failed_attempts"])
            if str(row["code"]) != code:
                new_failed = failed + 1
                if new_failed >= _MAX_FAILED_ATTEMPTS:
                    connection.execute(
                        """
                        UPDATE web_auth_codes
                        SET failed_attempts = ?, consumed_at = ?
                        WHERE id = ?
                        """,
                        (new_failed, now_iso, int(row["id"])),
                    )
                    return CodeVerification(
                        ok=False,
                        reason="too_many_attempts",
                        remaining_attempts=0,
                        chat_id=None,
                    )
                connection.execute(
                    """
                    UPDATE web_auth_codes
                    SET failed_attempts = ?
                    WHERE id = ?
                    """,
                    (new_failed, int(row["id"])),
                )
                return CodeVerification(
                    ok=False,
                    reason="invalid",
                    remaining_attempts=_MAX_FAILED_ATTEMPTS - new_failed,
                    chat_id=None,
                )
            connection.execute(
                "UPDATE web_auth_codes SET consumed_at = ? WHERE id = ?",
                (now_iso, int(row["id"])),
            )
            return CodeVerification(
                ok=True,
                reason=None,
                remaining_attempts=_MAX_FAILED_ATTEMPTS - failed,
                chat_id=int(row["chat_id"]),
            )

    def create_session(self, *, username: str, role: str) -> str:
        session_id = secrets.token_urlsafe(_SESSION_ID_BYTES)
        now_iso = _now_iso()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO web_sessions
                    (session_id, username, role, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, username, role, now_iso, now_iso),
            )
        return session_id

    def get_session(self, *, session_id: str) -> WebSession | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT session_id, username, role, created_at,
                       last_seen_at, revoked_at
                FROM web_sessions
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return WebSession(
            session_id=str(row["session_id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            created_at=str(row["created_at"]),
            last_seen_at=str(row["last_seen_at"]),
            revoked_at=(
                str(row["revoked_at"]) if row["revoked_at"] is not None else None
            ),
        )

    def revoke_session(self, *, session_id: str) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                "UPDATE web_sessions SET revoked_at = ? WHERE session_id = ?",
                (_now_iso(), session_id),
            )

    def revoke_all_for_username(self, *, username: str) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE web_sessions
                SET revoked_at = ?
                WHERE username = ? AND revoked_at IS NULL
                """,
                (_now_iso(), username),
            )

    def touch_session(self, *, session_id: str) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                "UPDATE web_sessions SET last_seen_at = ? WHERE session_id = ?",
                (_now_iso(), session_id),
            )
