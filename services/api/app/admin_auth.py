"""Admin authentication repository.

Owns the schema and lifecycle for short-lived login codes and opaque
session tokens used by the admin login flow. Codes and tokens are stored
as sha256 hashes; plaintext is returned to the caller exactly once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_CODE_DIGITS = "0123456789"
_CODE_LENGTH = 6
_TOKEN_NBYTES = 32


class InvalidLoginCode(Exception):
    """Raised when an admin login code cannot be consumed.

    Includes: unknown admin, wrong code, expired code, already-consumed
    code (replay). The handler maps this to an HTTP 401 without revealing
    which branch failed.
    """


@dataclass(frozen=True)
class AdminSession:
    token: str
    admin_username: str
    expires_at: str


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_DIGITS) for _ in range(_CODE_LENGTH))


def _generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_NBYTES)


class AdminAuthRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_login_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_username TEXT NOT NULL,
                    code_sha256 TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_codes_username "
                "ON admin_login_codes(admin_username)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_sha256 TEXT PRIMARY KEY,
                    admin_username TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def request_code(self, *, admin_username: str, ttl_seconds: int) -> str:
        now = _now()
        expires_at = _iso(now + timedelta(seconds=ttl_seconds))
        code = _generate_code()
        code_hash = _sha256(code)
        with _connect(self.db_path) as connection:
            # Invalidate prior unconsumed codes for the same admin.
            connection.execute(
                """
                UPDATE admin_login_codes
                SET consumed_at = ?
                WHERE admin_username = ? AND consumed_at IS NULL
                """,
                (_iso(now), admin_username),
            )
            connection.execute(
                """
                INSERT INTO admin_login_codes
                    (admin_username, code_sha256, expires_at, consumed_at, created_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (admin_username, code_hash, expires_at, _iso(now)),
            )
        return code

    def consume_code(
        self, *, admin_username: str, code: str, ttl_seconds: int
    ) -> AdminSession:
        now = _now()
        provided_hash = _sha256(code)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, code_sha256, expires_at
                FROM admin_login_codes
                WHERE admin_username = ? AND consumed_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (admin_username,),
            ).fetchone()
            if row is None:
                raise InvalidLoginCode("no_pending_code")
            stored_hash = str(row["code_sha256"])
            if not hmac.compare_digest(stored_hash, provided_hash):
                raise InvalidLoginCode("code_mismatch")
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            if expires_at <= now:
                raise InvalidLoginCode("code_expired")
            connection.execute(
                "UPDATE admin_login_codes SET consumed_at = ? WHERE id = ?",
                (_iso(now), int(row["id"])),
            )
            token = _generate_token()
            session_expiry = _iso(now + timedelta(seconds=ttl_seconds))
            connection.execute(
                """
                INSERT INTO admin_sessions
                    (token_sha256, admin_username, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (_sha256(token), admin_username, session_expiry, _iso(now)),
            )
        return AdminSession(
            token=token, admin_username=admin_username, expires_at=session_expiry
        )

    def validate_session(self, token: str) -> AdminSession | None:
        token_hash = _sha256(token)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT admin_username, expires_at
                FROM admin_sessions
                WHERE token_sha256 = ?
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(str(row["expires_at"]))
        if expires_at <= _now():
            return None
        return AdminSession(
            token=token,
            admin_username=str(row["admin_username"]),
            expires_at=str(row["expires_at"]),
        )

    def revoke_session(self, token: str) -> None:
        token_hash = _sha256(token)
        with _connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM admin_sessions WHERE token_sha256 = ?",
                (token_hash,),
            )

    def purge_expired(self) -> int:
        now_iso = _iso(_now())
        with _connect(self.db_path) as connection:
            codes_cursor = connection.execute(
                "DELETE FROM admin_login_codes WHERE expires_at <= ?",
                (now_iso,),
            )
            sessions_cursor = connection.execute(
                "DELETE FROM admin_sessions WHERE expires_at <= ?",
                (now_iso,),
            )
            removed = (codes_cursor.rowcount or 0) + (sessions_cursor.rowcount or 0)
        return removed
