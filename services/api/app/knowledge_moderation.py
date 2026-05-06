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


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
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


@dataclass(frozen=True)
class KnowledgeCandidateRow:
    id: int
    candidate_text: str
    published_text: str | None
    status: str
    created_at: str
    updated_at: str
    source_extraction_candidate_id: int | None


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

    def list_by_status(self, status: str | None) -> list[KnowledgeCandidateRow]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT id, candidate_text, published_text, status, created_at, updated_at,
                           source_extraction_candidate_id
                    FROM knowledge_moderation_candidates
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, candidate_text, published_text, status, created_at, updated_at,
                           source_extraction_candidate_id
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
                """
                SELECT id, candidate_text, published_text, status, created_at, updated_at,
                       source_extraction_candidate_id
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
        )
