from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BRANCH_PUBLISH = "publish"
BRANCH_MODERATION = "moderation"

STATUS_PUBLISHED = "published"
STATUS_PENDING_MODERATION = "pending_moderation"


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
            CREATE TABLE IF NOT EXISTS trace_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                status TEXT NOT NULL,
                draft_text TEXT NOT NULL,
                source_id TEXT,
                candidate_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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


@dataclass(frozen=True)
class TraceCorrection:
    id: int
    trace_id: str
    tenant_id: str
    user_id: str
    branch: str
    status: str
    draft_text: str
    source_id: str | None
    candidate_id: int | None
    created_at: str
    updated_at: str


class TraceCorrectionError(RuntimeError):
    pass


class TraceCorrectionRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def record_open(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        user_id: str,
    ) -> None:
        if not trace_id or not tenant_id or not user_id:
            raise TraceCorrectionError("required_fields")
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO nl_audit_logs (
                    tenant_id, user_id, session_id, op_type, details, created_at
                ) VALUES (?, ?, ?, 'trace_opened', ?, ?)
                """,
                (
                    tenant_id,
                    user_id,
                    None,
                    json.dumps({"trace_id": trace_id}),
                    _now(),
                ),
            )

    def submit_publish(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        user_id: str,
        edited_text: str,
    ) -> TraceCorrection:
        return self._submit(
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            edited_text=edited_text,
            branch=BRANCH_PUBLISH,
            status=STATUS_PUBLISHED,
        )

    def submit_moderation(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        user_id: str,
        edited_text: str,
        candidate_id: int,
    ) -> TraceCorrection:
        return self._submit(
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            edited_text=edited_text,
            branch=BRANCH_MODERATION,
            status=STATUS_PENDING_MODERATION,
            candidate_id=candidate_id,
        )

    def list_for_trace(self, trace_id: str) -> list[TraceCorrection]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, trace_id, tenant_id, user_id, branch, status, draft_text,
                       source_id, candidate_id, created_at, updated_at
                FROM trace_corrections
                WHERE trace_id = ?
                ORDER BY id ASC
                """,
                (trace_id,),
            ).fetchall()
        return [self._row_to_correction(row) for row in rows]

    def list_audit(self, *, trace_id: str | None = None) -> list[dict[str, object]]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, tenant_id, user_id, session_id, op_type, details, created_at
                FROM nl_audit_logs
                ORDER BY id ASC
                """
            ).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["details"]))
            except (TypeError, ValueError):  # pragma: no cover - defensive
                payload = {}
            if trace_id is not None and payload.get("trace_id") != trace_id:
                continue
            out.append(
                {
                    "id": int(row["id"]),
                    "tenant_id": str(row["tenant_id"]),
                    "user_id": str(row["user_id"]),
                    "op_type": str(row["op_type"]),
                    "details": payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return out

    def _submit(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        user_id: str,
        edited_text: str,
        branch: str,
        status: str,
        candidate_id: int | None = None,
    ) -> TraceCorrection:
        if not trace_id or not tenant_id or not user_id:
            raise TraceCorrectionError("required_fields")
        if not edited_text.strip():
            raise TraceCorrectionError("edited_text_required")
        init_schema(self.db_path)
        now = _now()
        source_id = (
            f"trace_correction:{tenant_id}:{trace_id}" if branch == BRANCH_PUBLISH else None
        )
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO trace_corrections (
                    trace_id, tenant_id, user_id, branch, status, draft_text,
                    source_id, candidate_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    tenant_id,
                    user_id,
                    branch,
                    status,
                    edited_text,
                    source_id,
                    candidate_id,
                    now,
                    now,
                ),
            )
            correction_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO nl_audit_logs (
                    tenant_id, user_id, session_id, op_type, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    user_id,
                    None,
                    f"correction_{status}",
                    json.dumps(
                        {
                            "trace_id": trace_id,
                            "branch": branch,
                            "correction_id": correction_id,
                            "candidate_id": candidate_id,
                            "source_id": source_id,
                        }
                    ),
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, trace_id, tenant_id, user_id, branch, status, draft_text,
                       source_id, candidate_id, created_at, updated_at
                FROM trace_corrections
                WHERE id = ?
                """,
                (correction_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_correction(row)

    @staticmethod
    def _row_to_correction(row: sqlite3.Row) -> TraceCorrection:
        return TraceCorrection(
            id=int(row["id"]),
            trace_id=str(row["trace_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            branch=str(row["branch"]),
            status=str(row["status"]),
            draft_text=str(row["draft_text"]),
            source_id=str(row["source_id"]) if row["source_id"] else None,
            candidate_id=(
                int(row["candidate_id"]) if row["candidate_id"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
