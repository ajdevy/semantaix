from __future__ import annotations

import sqlite3
from dataclasses import dataclass
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


def _moderation_column_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(knowledge_moderation_candidates)").fetchall()
    return {str(r["name"]) for r in rows}


_OPERATOR_UPLOAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("uploaded_by_operator_username", "TEXT"),
    ("is_confidential", "INTEGER NOT NULL DEFAULT 0"),
    ("source_file_name", "TEXT"),
    ("source_file_type", "TEXT"),
    ("stored_binary_path", "TEXT"),
    ("binary_sha256", "TEXT"),
    ("project_id", "INTEGER"),
    ("operator_short_id", "TEXT"),
)


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_moderation_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_text TEXT NOT NULL,
                published_text TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_extraction_candidate_id INTEGER
            )
            """
        )
        if connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'knowledge_moderation_candidates'
            """
        ).fetchone():
            columns = _moderation_column_names(connection)
            if "source_extraction_candidate_id" not in columns:
                connection.execute(
                    "ALTER TABLE knowledge_moderation_candidates "
                    "ADD COLUMN source_extraction_candidate_id INTEGER"
                )
            for column_name, column_decl in _OPERATOR_UPLOAD_COLUMNS:
                if column_name not in columns:
                    connection.execute(
                        f"ALTER TABLE knowledge_moderation_candidates "
                        f"ADD COLUMN {column_name} {column_decl}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kmc_binary_sha256 "
                "ON knowledge_moderation_candidates(binary_sha256)"
            )


@dataclass(frozen=True)
class KnowledgeCandidateRow:
    id: int
    candidate_text: str
    published_text: str | None
    status: str
    created_at: str
    updated_at: str
    source_extraction_candidate_id: int | None
    uploaded_by_operator_username: str | None = None
    is_confidential: bool = False
    source_file_name: str | None = None
    source_file_type: str | None = None
    stored_binary_path: str | None = None
    binary_sha256: str | None = None
    project_id: int | None = None
    operator_short_id: str | None = None


_SELECT_COLUMNS = (
    "id, candidate_text, published_text, status, created_at, updated_at, "
    "source_extraction_candidate_id, uploaded_by_operator_username, is_confidential, "
    "source_file_name, source_file_type, stored_binary_path, binary_sha256, "
    "project_id, operator_short_id"
)


class KnowledgeModerationRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def create_pending(
        self,
        *,
        text: str,
        source_extraction_candidate_id: int | None = None,
    ) -> KnowledgeCandidateRow:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_moderation_candidates (
                    candidate_text,
                    published_text,
                    status,
                    created_at,
                    updated_at,
                    source_extraction_candidate_id
                )
                VALUES (?, NULL, 'pending', ?, ?, ?)
                """,
                (text.strip(), now, now, source_extraction_candidate_id),
            )
            row_id = int(cursor.lastrowid)
        return self.get(row_id)

    def create_approved_operator_upload(
        self,
        *,
        candidate_text: str,
        published_text: str,
        operator_username: str,
        is_confidential: bool,
        source_file_name: str | None,
        source_file_type: str,
        stored_binary_path: str | None,
        binary_sha256: str | None,
        operator_short_id: str | None = None,
    ) -> KnowledgeCandidateRow:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_moderation_candidates (
                    candidate_text,
                    published_text,
                    status,
                    created_at,
                    updated_at,
                    source_extraction_candidate_id,
                    uploaded_by_operator_username,
                    is_confidential,
                    source_file_name,
                    source_file_type,
                    stored_binary_path,
                    binary_sha256,
                    operator_short_id
                )
                VALUES (?, ?, 'approved', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_text,
                    published_text,
                    now,
                    now,
                    operator_username,
                    1 if is_confidential else 0,
                    source_file_name,
                    source_file_type,
                    stored_binary_path,
                    binary_sha256,
                    operator_short_id,
                ),
            )
            row_id = int(cursor.lastrowid)
        return self.get(row_id)

    def find_by_operator_short_id(
        self, short_id: str
    ) -> KnowledgeCandidateRow | None:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM knowledge_moderation_candidates
                WHERE operator_short_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (short_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_candidate(row)

    def find_by_binary_sha256(self, sha256: str) -> KnowledgeCandidateRow | None:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM knowledge_moderation_candidates
                WHERE binary_sha256 = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_candidate(row)

    def list_by_status(self, status: str | None) -> list[KnowledgeCandidateRow]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if status is None:
                rows = connection.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM knowledge_moderation_candidates
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM knowledge_moderation_candidates
                    WHERE status = ?
                    ORDER BY id ASC
                    """,
                    (status,),
                ).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def get(self, candidate_id: int) -> KnowledgeCandidateRow:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM knowledge_moderation_candidates
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise LookupError("candidate_not_found")
        return self._row_to_candidate(row)

    def prepare_publish_text(self, *, candidate_id: int, edited_text: str | None) -> str:
        """Validate pending state and return text to index. Does not mutate storage."""
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT candidate_text, status FROM knowledge_moderation_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LookupError("candidate_not_found")
            if str(row["status"]) != "pending":
                raise ValueError("invalid_status")
            original = str(row["candidate_text"])
            if edited_text is not None and edited_text.strip():
                final = edited_text.strip()
            else:
                final = original
            if not final.strip():
                raise ValueError("empty_publish_text")
        return final

    def mark_approved(self, *, candidate_id: int, published_text: str) -> None:
        now = _now()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT status FROM knowledge_moderation_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LookupError("candidate_not_found")
            if str(row["status"]) != "pending":
                raise ValueError("invalid_status")
            connection.execute(
                """
                UPDATE knowledge_moderation_candidates
                SET status = 'approved', published_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (published_text, now, candidate_id),
            )

    def set_project_id(self, *, candidate_id: int, project_id: int) -> None:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_moderation_candidates
                SET project_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (project_id, now, candidate_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("candidate_not_found")

    def reject(self, *, candidate_id: int) -> None:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT status FROM knowledge_moderation_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LookupError("candidate_not_found")
            if str(row["status"]) != "pending":
                raise ValueError("invalid_status")
            connection.execute(
                """
                UPDATE knowledge_moderation_candidates
                SET status = 'rejected', updated_at = ?
                WHERE id = ?
                """,
                (now, candidate_id),
            )

    @staticmethod
    def _row_to_candidate(row: sqlite3.Row) -> KnowledgeCandidateRow:
        extraction_id = row["source_extraction_candidate_id"]
        is_conf_raw = row["is_confidential"]
        return KnowledgeCandidateRow(
            id=int(row["id"]),
            candidate_text=str(row["candidate_text"]),
            published_text=str(row["published_text"]) if row["published_text"] else None,
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            source_extraction_candidate_id=int(extraction_id)
            if extraction_id is not None
            else None,
            uploaded_by_operator_username=(
                str(row["uploaded_by_operator_username"])
                if row["uploaded_by_operator_username"]
                else None
            ),
            is_confidential=bool(is_conf_raw) if is_conf_raw is not None else False,
            source_file_name=str(row["source_file_name"]) if row["source_file_name"] else None,
            source_file_type=str(row["source_file_type"]) if row["source_file_type"] else None,
            stored_binary_path=(
                str(row["stored_binary_path"]) if row["stored_binary_path"] else None
            ),
            binary_sha256=str(row["binary_sha256"]) if row["binary_sha256"] else None,
            project_id=(
                int(row["project_id"]) if row["project_id"] is not None else None
            ),
            operator_short_id=(
                str(row["operator_short_id"])
                if row["operator_short_id"] is not None
                else None
            ),
        )
