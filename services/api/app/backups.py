from __future__ import annotations

import sqlite3
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


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
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                archive_path TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                source_paths TEXT NOT NULL,
                included_paths TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (backup_id) REFERENCES backups(id)
            )
            """
        )


@dataclass(frozen=True)
class Backup:
    id: int
    started_at: str
    completed_at: str | None
    status: str
    archive_path: str | None
    size_bytes: int
    source_paths: list[str]
    included_paths: list[str]
    error_message: str | None


@dataclass(frozen=True)
class RestoreResult:
    backup_id: int
    restored_paths: list[str]


class BackupError(RuntimeError):
    """Raised when a backup or restore operation fails irrecoverably."""


class BackupRepository:
    def __init__(
        self,
        *,
        db_path: str,
        archive_dir: str,
        source_paths: list[str],
    ) -> None:
        self.db_path = db_path
        self.archive_dir = archive_dir
        self.source_paths = source_paths
        init_schema(db_path)

    def run_backup(self) -> Backup:
        init_schema(self.db_path)
        archive_root = Path(self.archive_dir)
        archive_root.mkdir(parents=True, exist_ok=True)
        started = _now()
        sources_csv = ",".join(self.source_paths)
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO backups (
                    started_at, completed_at, status, archive_path,
                    size_bytes, source_paths, included_paths, error_message
                )
                VALUES (?, NULL, 'pending', NULL, 0, ?, '', NULL)
                """,
                (started.isoformat(), sources_csv),
            )
            backup_id = int(cursor.lastrowid)
            self._add_event(
                connection,
                backup_id=backup_id,
                event_type="backup_started",
                details=f"sources={sources_csv}",
                timestamp=started.isoformat(),
            )

        archive_name = f"backup_{backup_id}_{started.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
        archive_path = archive_root / archive_name
        included: list[str] = []
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                for source in self.source_paths:
                    source_path = Path(source)
                    if not source_path.exists():
                        continue
                    tar.add(source_path, arcname=source_path.name)
                    included.append(str(source_path))
            size_bytes = archive_path.stat().st_size
        except OSError as exc:
            error_text = f"backup_archive_failed: {exc}"
            with _connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE backups
                    SET status = 'failed', completed_at = ?, error_message = ?
                    WHERE id = ?
                    """,
                    (_now().isoformat(), error_text, backup_id),
                )
                self._add_event(
                    connection,
                    backup_id=backup_id,
                    event_type="backup_failed",
                    details=error_text,
                    timestamp=_now().isoformat(),
                )
            raise BackupError(error_text) from exc

        completed = _now()
        included_csv = ",".join(included)
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE backups
                SET status = 'success', completed_at = ?, archive_path = ?,
                    size_bytes = ?, included_paths = ?
                WHERE id = ?
                """,
                (completed.isoformat(), str(archive_path), size_bytes, included_csv, backup_id),
            )
            self._add_event(
                connection,
                backup_id=backup_id,
                event_type="backup_completed",
                details=f"archive={archive_path};size_bytes={size_bytes};included={included_csv}",
                timestamp=completed.isoformat(),
            )
        return self.get(backup_id)

    def get(self, backup_id: int) -> Backup:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, started_at, completed_at, status, archive_path,
                       size_bytes, source_paths, included_paths, error_message
                FROM backups
                WHERE id = ?
                """,
                (backup_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"backup_not_found:{backup_id}")
        return self._row_to_backup(row)

    def list_backups(self) -> list[Backup]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, started_at, completed_at, status, archive_path,
                       size_bytes, source_paths, included_paths, error_message
                FROM backups
                ORDER BY id DESC
                """
            ).fetchall()
        return [self._row_to_backup(row) for row in rows]

    def latest_successful(self) -> Backup | None:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, started_at, completed_at, status, archive_path,
                       size_bytes, source_paths, included_paths, error_message
                FROM backups
                WHERE status = 'success'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return self._row_to_backup(row)

    def restore(self, *, backup_id: int, confirm_token: str, target_root: str) -> RestoreResult:
        backup = self.get(backup_id)
        if backup.status != "success":
            raise BackupError(f"backup_not_restorable:status={backup.status}")
        if confirm_token != f"restore-{backup_id}":
            raise BackupError("invalid_confirm_token")
        if backup.archive_path is None:
            raise BackupError("missing_archive_path")
        archive_path = Path(backup.archive_path)
        if not archive_path.exists():
            raise BackupError("archive_missing_on_disk")

        target = Path(target_root)
        target.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(target, filter="data")
        except (OSError, tarfile.TarError) as exc:
            with _connect(self.db_path) as connection:
                self._add_event(
                    connection,
                    backup_id=backup_id,
                    event_type="restore_failed",
                    details=f"target={target_root};error={exc}",
                    timestamp=_now().isoformat(),
                )
            raise BackupError(f"restore_extract_failed: {exc}") from exc

        restored = sorted(str(p) for p in target.iterdir() if p.is_file())
        with _connect(self.db_path) as connection:
            self._add_event(
                connection,
                backup_id=backup_id,
                event_type="restore_completed",
                details=f"target={target_root};restored={','.join(restored)}",
                timestamp=_now().isoformat(),
            )
        return RestoreResult(backup_id=backup_id, restored_paths=restored)

    @staticmethod
    def _add_event(
        connection: sqlite3.Connection,
        *,
        backup_id: int,
        event_type: str,
        details: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO backup_events (backup_id, event_type, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (backup_id, event_type, details, timestamp),
        )

    @staticmethod
    def _row_to_backup(row: sqlite3.Row) -> Backup:
        sources_value = str(row["source_paths"]) if row["source_paths"] else ""
        included_value = str(row["included_paths"]) if row["included_paths"] else ""
        return Backup(
            id=int(row["id"]),
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
            status=str(row["status"]),
            archive_path=str(row["archive_path"]) if row["archive_path"] else None,
            size_bytes=int(row["size_bytes"]),
            source_paths=[item for item in sources_value.split(",") if item],
            included_paths=[item for item in included_value.split(",") if item],
            error_message=str(row["error_message"]) if row["error_message"] else None,
        )
