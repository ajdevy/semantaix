from __future__ import annotations

import sqlite3
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
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                severity TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_incident_fingerprint
            ON incidents(fingerprint) WHERE status = 'open'
            """
        )


@dataclass(frozen=True)
class Incident:
    id: int
    fingerprint: str
    severity: str
    summary: str
    status: str
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str


class IncidentRepository:
    def __init__(self, db_path: str, dedup_window_seconds: int) -> None:
        self.db_path = db_path
        self.dedup_window_seconds = dedup_window_seconds
        init_schema(db_path)

    def ingest(self, *, fingerprint: str, severity: str, summary: str) -> Incident:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            active = connection.execute(
                """
                SELECT id, occurrence_count, last_seen_at
                FROM incidents
                WHERE fingerprint = ? AND status = 'open'
                """,
                (fingerprint,),
            ).fetchone()

            if active is not None:
                last_seen = datetime.fromisoformat(str(active["last_seen_at"]))
                elapsed = (now - last_seen).total_seconds()
                if elapsed <= self.dedup_window_seconds:
                    connection.execute(
                        """
                        UPDATE incidents
                        SET occurrence_count = occurrence_count + 1, last_seen_at = ?
                        WHERE id = ?
                        """,
                        (now.isoformat(), int(active["id"])),
                    )
                    row = connection.execute(
                        """
                        SELECT id, fingerprint, severity, summary, status, occurrence_count,
                               first_seen_at, last_seen_at
                        FROM incidents
                        WHERE id = ?
                        """,
                        (int(active["id"]),),
                    ).fetchone()
                    assert row is not None
                    return self._row_to_incident(row)

                connection.execute(
                    "UPDATE incidents SET status = 'resolved' WHERE id = ?",
                    (int(active["id"]),),
                )

            cursor = connection.execute(
                """
                INSERT INTO incidents (
                    fingerprint, severity, summary, status, occurrence_count,
                    first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, 'open', 1, ?, ?)
                """,
                (fingerprint, severity, summary, now.isoformat(), now.isoformat()),
            )
            row = connection.execute(
                """
                SELECT id, fingerprint, severity, summary, status, occurrence_count,
                       first_seen_at, last_seen_at
                FROM incidents
                WHERE id = ?
                """,
                (int(cursor.lastrowid),),
            ).fetchone()
            assert row is not None
            return self._row_to_incident(row)

    def get_by_fingerprint(self, fingerprint: str) -> list[Incident]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, fingerprint, severity, summary, status, occurrence_count,
                       first_seen_at, last_seen_at
                FROM incidents
                WHERE fingerprint = ?
                ORDER BY id ASC
                """,
                (fingerprint,),
            ).fetchall()
            return [self._row_to_incident(row) for row in rows]

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            id=int(row["id"]),
            fingerprint=str(row["fingerprint"]),
            severity=str(row["severity"]),
            summary=str(row["summary"]),
            status=str(row["status"]),
            occurrence_count=int(row["occurrence_count"]),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )
