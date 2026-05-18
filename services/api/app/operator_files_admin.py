"""Cascade-delete writer for operator file uploads.

Companion to :mod:`operator_files_view` — that module reads in read-only mode
across three databases; this one performs a cross-database cascade DELETE in a
single transaction.

Cascade scope for a single ``short_id``:

* ``rag_chunks`` rows whose ``source_id = 'knowledge_candidate:<id>'``
* ``knowledge_moderation_candidates`` row by ``id``
* ``operator_files`` row by ``short_id``
* ``stored_binary_path`` on disk (best-effort, post-commit)

The ``source_id`` format matches every other producer in the codebase
(``_perform_operator_upload`` and ``approve_knowledge_candidate`` in
``services/api/app/main.py``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _rag_source_id(knowledge_candidate_id: int) -> str:
    return f"knowledge_candidate:{knowledge_candidate_id}"


@dataclass(frozen=True)
class DeletedFileSummary:
    deleted_files: int
    deleted_chunks: int
    deleted_candidates: int
    deleted_binaries: int
    failed_binary_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _AffectedRow:
    short_id: str
    knowledge_candidate_id: int | None
    stored_binary_path: str | None


def _open_writer(
    *,
    operator_files_db_path: str,
    knowledge_db_path: str,
    rag_db_path: str,
) -> sqlite3.Connection:
    if not Path(operator_files_db_path).exists():
        raise FileNotFoundError(operator_files_db_path)
    connection = sqlite3.connect(operator_files_db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        f"ATTACH DATABASE '{knowledge_db_path}' AS kdb"
    )
    connection.execute(
        f"ATTACH DATABASE '{rag_db_path}' AS rdb"
    )
    return connection


class OperatorFilesAdminWriter:
    """Cascade-delete operator files across operator_files / kdb / rdb."""

    def __init__(
        self,
        *,
        operator_files_db_path: str,
        knowledge_db_path: str,
        rag_db_path: str,
    ) -> None:
        self.operator_files_db_path = operator_files_db_path
        self.knowledge_db_path = knowledge_db_path
        self.rag_db_path = rag_db_path

    def delete(
        self,
        *,
        short_id: str,
        viewer_username: str,
        viewer_role: str,
    ) -> DeletedFileSummary | None:
        """Delete a single operator file by short_id.

        Returns ``None`` when no row matches the caller's scope (so the route
        can emit a 404). Admin sees every row; operator sees only own rows.
        """
        with _open_writer(
            operator_files_db_path=self.operator_files_db_path,
            knowledge_db_path=self.knowledge_db_path,
            rag_db_path=self.rag_db_path,
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                affected = self._collect_for_short_id(
                    connection,
                    short_id=short_id,
                    viewer_username=viewer_username,
                    viewer_role=viewer_role,
                )
                if not affected:
                    connection.execute("ROLLBACK")
                    return None
                summary_counts = self._cascade_delete(connection, affected)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        binaries_deleted, failures = _unlink_binaries(affected)
        return DeletedFileSummary(
            deleted_files=summary_counts["files"],
            deleted_chunks=summary_counts["chunks"],
            deleted_candidates=summary_counts["candidates"],
            deleted_binaries=binaries_deleted,
            failed_binary_paths=failures,
        )

    def delete_all_for_user(self, *, username: str) -> DeletedFileSummary:
        """Bulk-delete every file owned by ``username``.

        Even admin uses this path on their own username — the per-file
        ``delete`` route is the way to clear someone else's file.
        """
        with _open_writer(
            operator_files_db_path=self.operator_files_db_path,
            knowledge_db_path=self.knowledge_db_path,
            rag_db_path=self.rag_db_path,
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                affected = self._collect_for_username(
                    connection, username=username
                )
                if not affected:
                    connection.execute("ROLLBACK")
                    return DeletedFileSummary(
                        deleted_files=0,
                        deleted_chunks=0,
                        deleted_candidates=0,
                        deleted_binaries=0,
                        failed_binary_paths=[],
                    )
                summary_counts = self._cascade_delete(connection, affected)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        binaries_deleted, failures = _unlink_binaries(affected)
        return DeletedFileSummary(
            deleted_files=summary_counts["files"],
            deleted_chunks=summary_counts["chunks"],
            deleted_candidates=summary_counts["candidates"],
            deleted_binaries=binaries_deleted,
            failed_binary_paths=failures,
        )

    @staticmethod
    def _collect_for_short_id(
        connection: sqlite3.Connection,
        *,
        short_id: str,
        viewer_username: str,
        viewer_role: str,
    ) -> list[_AffectedRow]:
        if viewer_role == "admin":
            row = connection.execute(
                """
                SELECT short_id, knowledge_candidate_id, stored_binary_path
                FROM operator_files
                WHERE short_id = ?
                """,
                (short_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT short_id, knowledge_candidate_id, stored_binary_path
                FROM operator_files
                WHERE short_id = ? AND username = ?
                """,
                (short_id, viewer_username),
            ).fetchone()
        if row is None:
            return []
        return [
            _AffectedRow(
                short_id=str(row["short_id"]),
                knowledge_candidate_id=(
                    int(row["knowledge_candidate_id"])
                    if row["knowledge_candidate_id"] is not None
                    else None
                ),
                stored_binary_path=(
                    str(row["stored_binary_path"])
                    if row["stored_binary_path"] is not None
                    else None
                ),
            )
        ]

    @staticmethod
    def _collect_for_username(
        connection: sqlite3.Connection, *, username: str
    ) -> list[_AffectedRow]:
        rows = connection.execute(
            """
            SELECT short_id, knowledge_candidate_id, stored_binary_path
            FROM operator_files
            WHERE username = ?
            """,
            (username,),
        ).fetchall()
        return [
            _AffectedRow(
                short_id=str(row["short_id"]),
                knowledge_candidate_id=(
                    int(row["knowledge_candidate_id"])
                    if row["knowledge_candidate_id"] is not None
                    else None
                ),
                stored_binary_path=(
                    str(row["stored_binary_path"])
                    if row["stored_binary_path"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _cascade_delete(
        connection: sqlite3.Connection, affected: list[_AffectedRow]
    ) -> dict[str, int]:
        candidate_ids = [
            row.knowledge_candidate_id
            for row in affected
            if row.knowledge_candidate_id is not None
        ]
        rag_source_ids = [_rag_source_id(c) for c in candidate_ids]
        short_ids = [row.short_id for row in affected]

        deleted_chunks = 0
        deleted_candidates = 0
        if rag_source_ids:
            placeholders = ",".join("?" for _ in rag_source_ids)
            cursor = connection.execute(
                f"DELETE FROM rdb.rag_chunks WHERE source_id IN ({placeholders})",
                rag_source_ids,
            )
            deleted_chunks = int(cursor.rowcount or 0)
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            cursor = connection.execute(
                "DELETE FROM kdb.knowledge_moderation_candidates "
                f"WHERE id IN ({placeholders})",
                candidate_ids,
            )
            deleted_candidates = int(cursor.rowcount or 0)
        placeholders = ",".join("?" for _ in short_ids)
        cursor = connection.execute(
            f"DELETE FROM operator_files WHERE short_id IN ({placeholders})",
            short_ids,
        )
        deleted_files = int(cursor.rowcount or 0)
        return {
            "files": deleted_files,
            "chunks": deleted_chunks,
            "candidates": deleted_candidates,
        }


def _unlink_binaries(affected: list[_AffectedRow]) -> tuple[int, list[str]]:
    """Best-effort post-commit unlink. Stale files on disk are harmless."""
    deleted = 0
    failures: list[str] = []
    for row in affected:
        if row.stored_binary_path is None:
            continue
        try:
            os.unlink(row.stored_binary_path)
            deleted += 1
        except FileNotFoundError:
            # Already gone — count as success, the goal state is "absent".
            deleted += 1
        except OSError as exc:
            logger.warning(
                "operator_file_binary_unlink_failed",
                extra={
                    "path": row.stored_binary_path,
                    "short_id": row.short_id,
                    "error": str(exc),
                },
            )
            failures.append(row.stored_binary_path)
    return deleted, failures
