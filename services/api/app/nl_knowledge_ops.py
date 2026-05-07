from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

INTENT_CREATE = "create"
INTENT_UPDATE = "update"
INTENT_DEPRECATE = "deprecate"
INTENT_CLARIFY = "clarify"

STATUS_PENDING = "pending_confirmation"
STATUS_CLARIFY = "clarify"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"

_CREATE_KEYWORDS = {"add", "create", "insert", "new"}
_UPDATE_KEYWORDS = {"update", "change", "edit", "modify"}
_DEPRECATE_KEYWORDS = {"delete", "deprecate", "remove", "retire"}


def _now() -> datetime:
    return datetime.now(UTC)


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_op_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                utterance TEXT NOT NULL,
                intent TEXT NOT NULL,
                draft_text TEXT NOT NULL,
                status TEXT NOT NULL,
                confirm_token TEXT,
                knowledge_version_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                status TEXT NOT NULL,
                nl_session_id INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS nl_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id INTEGER,
                op_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def parse_intent(utterance: str) -> tuple[str, str]:
    text = utterance.strip()
    if not text:
        return INTENT_CLARIFY, ""
    head, _, tail = text.partition(" ")
    head_lower = head.lower()
    if head_lower in _CREATE_KEYWORDS:
        return INTENT_CREATE, tail.strip()
    if head_lower in _UPDATE_KEYWORDS:
        return INTENT_UPDATE, tail.strip()
    if head_lower in _DEPRECATE_KEYWORDS:
        return INTENT_DEPRECATE, tail.strip()
    return INTENT_CLARIFY, text


@dataclass(frozen=True)
class NlOpSession:
    id: int
    tenant_id: str
    user_id: str
    utterance: str
    intent: str
    draft_text: str
    status: str
    confirm_token: str | None
    knowledge_version_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeVersion:
    id: int
    tenant_id: str
    version_number: int
    source_text: str
    status: str
    nl_session_id: int
    source_id: str
    created_at: str


@dataclass(frozen=True)
class NlAuditLog:
    id: int
    tenant_id: str
    user_id: str
    session_id: int | None
    op_type: str
    details: str
    created_at: str


class NlKnowledgeOpsError(RuntimeError):
    pass


class NlKnowledgeOpsRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def propose(
        self,
        *,
        tenant_id: str,
        user_id: str,
        utterance: str,
    ) -> NlOpSession:
        if not tenant_id:
            raise NlKnowledgeOpsError("tenant_id_required")
        if not user_id:
            raise NlKnowledgeOpsError("user_id_required")
        if not utterance.strip():
            raise NlKnowledgeOpsError("utterance_required")
        init_schema(self.db_path)
        intent, draft = parse_intent(utterance)
        status = STATUS_CLARIFY if intent == INTENT_CLARIFY else STATUS_PENDING
        confirm_token: str | None = None
        if status == STATUS_PENDING:
            if not draft:
                # Mutating ops require a body to draft into a knowledge version.
                status = STATUS_CLARIFY
                intent = INTENT_CLARIFY
                draft = utterance.strip()
            else:
                confirm_token = uuid.uuid4().hex
        now = _now().isoformat()
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO nl_op_sessions (
                    tenant_id, user_id, utterance, intent, draft_text, status,
                    confirm_token, knowledge_version_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    tenant_id,
                    user_id,
                    utterance,
                    intent,
                    draft,
                    status,
                    confirm_token,
                    now,
                    now,
                ),
            )
            session_id = int(cursor.lastrowid)
            self._log(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                op_type=(
                    "preview_created" if status == STATUS_PENDING else "clarify_requested"
                ),
                details=json.dumps({"intent": intent, "draft_chars": len(draft)}),
                timestamp=now,
            )
            return self._fetch_session(connection, session_id)

    def confirm(
        self,
        *,
        session_id: int,
        confirm_token: str,
    ) -> tuple[NlOpSession, KnowledgeVersion]:
        init_schema(self.db_path)
        now_iso = _now().isoformat()
        with _connect(self.db_path) as connection:
            session = self._fetch_session(connection, session_id)
            if session.status == STATUS_CONFIRMED:
                raise NlKnowledgeOpsError("already_confirmed")
            if session.status != STATUS_PENDING:
                raise NlKnowledgeOpsError(f"invalid_status:{session.status}")
            if session.confirm_token is None or confirm_token != session.confirm_token:
                connection.execute(
                    """
                    INSERT INTO nl_audit_logs (
                        tenant_id, user_id, session_id, op_type, details, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.tenant_id,
                        session.user_id,
                        session.id,
                        "confirm_rejected",
                        json.dumps({"reason": "invalid_token"}),
                        now_iso,
                    ),
                )
                connection.commit()
                raise NlKnowledgeOpsError("invalid_confirm_token")
            next_version = (
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM knowledge_versions
                    WHERE tenant_id = ?
                    """,
                    (session.tenant_id,),
                ).fetchone()[0]
            )
            status = "published" if session.intent != INTENT_DEPRECATE else "deprecated"
            source_id = f"nl_op:{session.tenant_id}:{session.id}"
            cursor = connection.execute(
                """
                INSERT INTO knowledge_versions (
                    tenant_id, version_number, source_text, status,
                    nl_session_id, source_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.tenant_id,
                    int(next_version),
                    session.draft_text,
                    status,
                    session.id,
                    source_id,
                    now_iso,
                ),
            )
            version_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE nl_op_sessions
                SET status = ?, knowledge_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_CONFIRMED, version_id, now_iso, session.id),
            )
            self._log(
                connection,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
                session_id=session.id,
                op_type="confirmed",
                details=json.dumps(
                    {
                        "intent": session.intent,
                        "version_id": version_id,
                        "version_number": int(next_version),
                        "source_id": source_id,
                    }
                ),
                timestamp=now_iso,
            )
            updated_session = self._fetch_session(connection, session.id)
            version = self._fetch_version(connection, version_id)
            return updated_session, version

    def cancel(self, *, session_id: int) -> NlOpSession:
        init_schema(self.db_path)
        now_iso = _now().isoformat()
        with _connect(self.db_path) as connection:
            session = self._fetch_session(connection, session_id)
            if session.status not in {STATUS_PENDING, STATUS_CLARIFY}:
                raise NlKnowledgeOpsError(f"invalid_status:{session.status}")
            connection.execute(
                """
                UPDATE nl_op_sessions
                SET status = ?, updated_at = ?, confirm_token = NULL
                WHERE id = ?
                """,
                (STATUS_CANCELLED, now_iso, session.id),
            )
            self._log(
                connection,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
                session_id=session.id,
                op_type="cancelled",
                details=json.dumps({"prior_status": session.status}),
                timestamp=now_iso,
            )
            return self._fetch_session(connection, session.id)

    def get_session(self, session_id: int) -> NlOpSession:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            return self._fetch_session(connection, session_id)

    def list_sessions(self, *, tenant_id: str | None = None) -> list[NlOpSession]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if tenant_id is None:
                rows = connection.execute(
                    "SELECT * FROM nl_op_sessions ORDER BY id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM nl_op_sessions WHERE tenant_id = ? ORDER BY id DESC",
                    (tenant_id,),
                ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def list_versions(self, *, tenant_id: str | None = None) -> list[KnowledgeVersion]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if tenant_id is None:
                rows = connection.execute(
                    "SELECT * FROM knowledge_versions ORDER BY id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM knowledge_versions WHERE tenant_id = ? ORDER BY id DESC",
                    (tenant_id,),
                ).fetchall()
            return [self._row_to_version(row) for row in rows]

    def list_audit_logs(self, *, tenant_id: str | None = None) -> list[NlAuditLog]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if tenant_id is None:
                rows = connection.execute(
                    "SELECT * FROM nl_audit_logs ORDER BY id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM nl_audit_logs WHERE tenant_id = ? ORDER BY id ASC",
                    (tenant_id,),
                ).fetchall()
            return [
                NlAuditLog(
                    id=int(row["id"]),
                    tenant_id=str(row["tenant_id"]),
                    user_id=str(row["user_id"]),
                    session_id=int(row["session_id"]) if row["session_id"] is not None else None,
                    op_type=str(row["op_type"]),
                    details=str(row["details"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ]

    def _fetch_session(self, connection: sqlite3.Connection, session_id: int) -> NlOpSession:
        row = connection.execute(
            "SELECT * FROM nl_op_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"nl_op_session_not_found:{session_id}")
        return self._row_to_session(row)

    def _fetch_version(
        self, connection: sqlite3.Connection, version_id: int
    ) -> KnowledgeVersion:
        row = connection.execute(
            "SELECT * FROM knowledge_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        assert row is not None
        return self._row_to_version(row)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> NlOpSession:
        return NlOpSession(
            id=int(row["id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            utterance=str(row["utterance"]),
            intent=str(row["intent"]),
            draft_text=str(row["draft_text"]),
            status=str(row["status"]),
            confirm_token=str(row["confirm_token"]) if row["confirm_token"] else None,
            knowledge_version_id=(
                int(row["knowledge_version_id"])
                if row["knowledge_version_id"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> KnowledgeVersion:
        return KnowledgeVersion(
            id=int(row["id"]),
            tenant_id=str(row["tenant_id"]),
            version_number=int(row["version_number"]),
            source_text=str(row["source_text"]),
            status=str(row["status"]),
            nl_session_id=int(row["nl_session_id"]),
            source_id=str(row["source_id"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _log(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        user_id: str,
        session_id: int | None,
        op_type: str,
        details: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO nl_audit_logs (
                tenant_id, user_id, session_id, op_type, details, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, user_id, session_id, op_type, details, timestamp),
        )
