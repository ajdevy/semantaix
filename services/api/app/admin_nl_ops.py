"""Admin natural-language operations.

Story 10.05 — propose/confirm/cancel state machine + intent parsing for
admin DM phrases like "создай проект billing Биллинг" or
"привяжи файл #ABC к billing". The dispatch on confirm is owned by the
api endpoint so this module stays a pure persistence layer.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

STATUS_PENDING = "pending_confirmation"
STATUS_CLARIFY = "clarify"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

OP_PROJECT_CREATE = "project_create"
OP_PROJECT_RENAME = "project_rename"
OP_OPERATOR_ATTACH = "operator_attach"
OP_OPERATOR_DETACH = "operator_detach"
OP_FILE_ATTACH = "file_attach"
OP_CLARIFY = "clarify"

_DEFAULT_PENDING_TTL_SECONDS = 600


class InvalidConfirmToken(Exception):
    """Raised when a confirm call uses a wrong/missing token."""


class SessionNotPending(Exception):
    """Raised when confirm/cancel targets a session that is not pending."""


_PROJECT_CREATE_RE = re.compile(
    r"^\s*созда(?:й|йте)\s+проект\s+(?P<slug>\S+)(?:\s+(?P<name>.+))?\s*$",
    re.IGNORECASE,
)
_PROJECT_RENAME_RE = re.compile(
    r"^\s*переименуй(?:те)?\s+проект\s+(?P<slug>\S+)\s+в\s+(?P<name>.+)\s*$",
    re.IGNORECASE,
)
_OPERATOR_ATTACH_RE = re.compile(
    r"^\s*добавь(?:те)?\s+оператора\s+(?P<username>@\S+)\s+в\s+"
    r"(?P<project_slug>\S+)(?:\s+(?P<chat_id>\d+))?\s*$",
    re.IGNORECASE,
)
_OPERATOR_DETACH_RE = re.compile(
    r"^\s*удали(?:те)?\s+оператора\s+(?P<username>@\S+)\s*$", re.IGNORECASE
)
_FILE_ATTACH_RE = re.compile(
    r"^\s*привяжи(?:те)?\s+файл\s+#(?P<short_id>\S+)\s+к\s+"
    r"(?P<project_slug>\S+)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentMatch:
    op_type: str
    payload: dict[str, object]
    preview: str


def parse_intent(utterance: str) -> IntentMatch:
    """Map a Russian admin utterance to a structured op.

    Returns an `IntentMatch` with `op_type=OP_CLARIFY` and a hint preview
    when nothing matches.
    """
    m = _PROJECT_CREATE_RE.match(utterance)
    if m:
        slug = m.group("slug")
        name = (m.group("name") or slug).strip()
        return IntentMatch(
            op_type=OP_PROJECT_CREATE,
            payload={"slug": slug, "name": name},
            preview=f"Создать проект «{slug}» (название: {name}).",
        )
    m = _PROJECT_RENAME_RE.match(utterance)
    if m:
        slug = m.group("slug")
        name = m.group("name").strip()
        return IntentMatch(
            op_type=OP_PROJECT_RENAME,
            payload={"slug": slug, "name": name},
            preview=f"Переименовать проект «{slug}» в «{name}».",
        )
    m = _OPERATOR_ATTACH_RE.match(utterance)
    if m:
        chat_raw = m.group("chat_id")
        payload: dict[str, object] = {
            "username": m.group("username"),
            "project_slug": m.group("project_slug"),
        }
        if chat_raw:
            payload["chat_id"] = int(chat_raw)
        preview = (
            f"Добавить оператора {payload['username']} в проект "
            f"«{payload['project_slug']}»."
        )
        if chat_raw:
            preview += f" chat_id={chat_raw}."
        return IntentMatch(
            op_type=OP_OPERATOR_ATTACH, payload=payload, preview=preview
        )
    m = _OPERATOR_DETACH_RE.match(utterance)
    if m:
        return IntentMatch(
            op_type=OP_OPERATOR_DETACH,
            payload={"username": m.group("username")},
            preview=f"Деактивировать оператора {m.group('username')}.",
        )
    m = _FILE_ATTACH_RE.match(utterance)
    if m:
        return IntentMatch(
            op_type=OP_FILE_ATTACH,
            payload={
                "short_id": m.group("short_id"),
                "project_slug": m.group("project_slug"),
            },
            preview=(
                f"Привязать файл #{m.group('short_id')} "
                f"к проекту «{m.group('project_slug')}»."
            ),
        )
    return IntentMatch(
        op_type=OP_CLARIFY,
        payload={},
        preview=(
            "Не понял. Попробуйте: «создай проект <slug> <name>», "
            "«добавь оператора @user в <slug> [chat_id]», "
            "«удали оператора @user», или «привяжи файл #<short_id> к <slug>»."
        ),
    )


@dataclass(frozen=True)
class AdminNlOpSession:
    id: int
    admin_username: str
    utterance: str
    op_type: str
    payload: dict[str, object]
    status: str
    confirm_token: str | None
    preview: str
    created_at: str
    updated_at: str


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


class AdminNlOpsRepository:
    def __init__(
        self, db_path: str, *, pending_ttl_seconds: int = _DEFAULT_PENDING_TTL_SECONDS
    ) -> None:
        self.db_path = db_path
        self._pending_ttl = pending_ttl_seconds
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_nl_op_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_username TEXT NOT NULL,
                    utterance TEXT NOT NULL,
                    op_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confirm_token TEXT,
                    preview TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Idempotent additive ALTER for the `preview` column on dbs
            # created by 10.01's schema-only version.
            columns = {
                str(r["name"])
                for r in connection.execute(
                    "PRAGMA table_info(admin_nl_op_sessions)"
                ).fetchall()
            }
            if "preview" not in columns:
                connection.execute(
                    "ALTER TABLE admin_nl_op_sessions ADD COLUMN preview TEXT "
                    "NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_nl_ops_admin "
                "ON admin_nl_op_sessions(admin_username)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_nl_ops_status "
                "ON admin_nl_op_sessions(status)"
            )

    def propose(
        self, *, admin_username: str, utterance: str
    ) -> AdminNlOpSession:
        intent = parse_intent(utterance)
        now = _now()
        status = (
            STATUS_PENDING if intent.op_type != OP_CLARIFY else STATUS_CLARIFY
        )
        confirm_token = (
            secrets.token_urlsafe(16) if status == STATUS_PENDING else None
        )
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO admin_nl_op_sessions (
                    admin_username, utterance, op_type, payload_json,
                    status, confirm_token, preview, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    admin_username,
                    utterance,
                    intent.op_type,
                    json.dumps(intent.payload, ensure_ascii=False),
                    status,
                    confirm_token,
                    intent.preview,
                    _iso(now),
                    _iso(now),
                ),
            )
            row_id = int(cursor.lastrowid)
        return self._get(row_id)

    def get(self, session_id: int) -> AdminNlOpSession:
        session = self._maybe_get(session_id)
        if session is None:
            raise LookupError("session_not_found")
        # Lazy-expire pending sessions older than TTL.
        if session.status == STATUS_PENDING:
            created = datetime.fromisoformat(session.created_at)
            if _now() - created > timedelta(seconds=self._pending_ttl):
                with _connect(self.db_path) as connection:
                    connection.execute(
                        """
                        UPDATE admin_nl_op_sessions
                        SET status = ?, confirm_token = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (STATUS_EXPIRED, _iso(_now()), session_id),
                    )
                session = self._get(session_id)
        return session

    def confirm(
        self, *, session_id: int, confirm_token: str
    ) -> AdminNlOpSession:
        session = self.get(session_id)
        if session.status != STATUS_PENDING:
            raise SessionNotPending(session.status)
        import hmac

        if session.confirm_token is None or not hmac.compare_digest(
            session.confirm_token, confirm_token
        ):
            raise InvalidConfirmToken()
        now = _iso(_now())
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE admin_nl_op_sessions
                SET status = ?, confirm_token = NULL, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_CONFIRMED, now, session_id),
            )
        return self._get(session_id)

    def cancel(self, *, session_id: int) -> AdminNlOpSession:
        session = self.get(session_id)
        if session.status not in {STATUS_PENDING, STATUS_CLARIFY}:
            raise SessionNotPending(session.status)
        now = _iso(_now())
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE admin_nl_op_sessions
                SET status = ?, confirm_token = NULL, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_CANCELLED, now, session_id),
            )
        return self._get(session_id)

    def latest_pending_for(
        self, admin_username: str
    ) -> AdminNlOpSession | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, admin_username, utterance, op_type, payload_json,
                       status, confirm_token, preview, created_at, updated_at
                FROM admin_nl_op_sessions
                WHERE admin_username = ? AND status = ?
                ORDER BY id DESC LIMIT 1
                """,
                (admin_username, STATUS_PENDING),
            ).fetchone()
        if row is None:
            return None
        session = _row_to_session(row)
        # Apply lazy expiry.
        return self.get(session.id)

    def _maybe_get(self, session_id: int) -> AdminNlOpSession | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, admin_username, utterance, op_type, payload_json,
                       status, confirm_token, preview, created_at, updated_at
                FROM admin_nl_op_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return _row_to_session(row) if row is not None else None

    def _get(self, session_id: int) -> AdminNlOpSession:
        session = self._maybe_get(session_id)
        if session is None:
            raise LookupError("session_not_found")
        return session


def _row_to_session(row: sqlite3.Row) -> AdminNlOpSession:
    payload_raw = str(row["payload_json"])
    payload = json.loads(payload_raw) if payload_raw else {}
    return AdminNlOpSession(
        id=int(row["id"]),
        admin_username=str(row["admin_username"]),
        utterance=str(row["utterance"]),
        op_type=str(row["op_type"]),
        payload=payload,
        status=str(row["status"]),
        confirm_token=(
            str(row["confirm_token"])
            if row["confirm_token"] is not None
            else None
        ),
        preview=str(row["preview"]) if row["preview"] is not None else "",
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
