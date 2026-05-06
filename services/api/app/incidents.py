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
                is_read INTEGER NOT NULL,
                occurrence_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                acknowledged_at TEXT,
                resolved_at TEXT
            )
            """
        )
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
        ]
        if "is_read" not in columns:
            connection.execute(
                "ALTER TABLE incidents ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0"
            )
        if "acknowledged_at" not in columns:
            connection.execute("ALTER TABLE incidents ADD COLUMN acknowledged_at TEXT")
        if "resolved_at" not in columns:
            connection.execute("ALTER TABLE incidents ADD COLUMN resolved_at TEXT")

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_incident_fingerprint
            ON incidents(fingerprint) WHERE status = 'open'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (incident_id) REFERENCES incidents(id)
            )
            """
        )


@dataclass(frozen=True)
class Incident:
    id: int
    fingerprint: str
    severity: str
    summary: str
    status: str
    is_read: bool
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str
    acknowledged_at: str | None
    resolved_at: str | None


@dataclass(frozen=True)
class IncidentEvent:
    id: int
    incident_id: int
    event_type: str
    details: str
    created_at: str


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
                        SET occurrence_count = occurrence_count + 1, last_seen_at = ?, is_read = 0
                        WHERE id = ?
                        """,
                        (now.isoformat(), int(active["id"])),
                    )
                    self._add_event(
                        connection,
                        incident_id=int(active["id"]),
                        event_type="deduplicated",
                        details="Repeated event collapsed into active incident",
                        timestamp=now.isoformat(),
                    )
                    row = connection.execute(
                        """
                        SELECT id, fingerprint, severity, summary, status, is_read,
                               occurrence_count,
                               first_seen_at, last_seen_at, acknowledged_at, resolved_at
                        FROM incidents
                        WHERE id = ?
                        """,
                        (int(active["id"]),),
                    ).fetchone()
                    assert row is not None
                    return self._row_to_incident(row)

                connection.execute(
                    "UPDATE incidents SET status = 'resolved', resolved_at = ? WHERE id = ?",
                    (now.isoformat(), int(active["id"])),
                )
                self._add_event(
                    connection,
                    incident_id=int(active["id"]),
                    event_type="auto_resolved",
                    details="Prior incident resolved due to dedup window expiry",
                    timestamp=now.isoformat(),
                )

            cursor = connection.execute(
                """
                INSERT INTO incidents (
                    fingerprint, severity, summary, status, is_read, occurrence_count,
                    first_seen_at, last_seen_at, acknowledged_at, resolved_at
                )
                VALUES (?, ?, ?, 'open', 0, 1, ?, ?, NULL, NULL)
                """,
                (fingerprint, severity, summary, now.isoformat(), now.isoformat()),
            )
            self._add_event(
                connection,
                incident_id=int(cursor.lastrowid),
                event_type="created",
                details="Incident created from incoming event",
                timestamp=now.isoformat(),
            )
            row = connection.execute(
                """
                SELECT id, fingerprint, severity, summary, status, is_read, occurrence_count,
                       first_seen_at, last_seen_at, acknowledged_at, resolved_at
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
                SELECT id, fingerprint, severity, summary, status, is_read, occurrence_count,
                       first_seen_at, last_seen_at, acknowledged_at, resolved_at
                FROM incidents
                WHERE fingerprint = ?
                ORDER BY id ASC
                """,
                (fingerprint,),
            ).fetchall()
            return [self._row_to_incident(row) for row in rows]

    def list_incidents(self) -> list[Incident]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, fingerprint, severity, summary, status, is_read, occurrence_count,
                       first_seen_at, last_seen_at, acknowledged_at, resolved_at
                FROM incidents
                ORDER BY id DESC
                """
            ).fetchall()
            return [self._row_to_incident(row) for row in rows]

    def mark_read(self, incident_id: int) -> Incident:
        return self._transition(incident_id=incident_id, event_type="read")

    def acknowledge(self, incident_id: int) -> Incident:
        return self._transition(incident_id=incident_id, event_type="acknowledged")

    def resolve(self, incident_id: int) -> Incident:
        return self._transition(incident_id=incident_id, event_type="resolved")

    def get_timeline(self, incident_id: int) -> list[IncidentEvent]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, incident_id, event_type, details, created_at
                FROM incident_events
                WHERE incident_id = ?
                ORDER BY id ASC
                """,
                (incident_id,),
            ).fetchall()
            return [
                IncidentEvent(
                    id=int(row["id"]),
                    incident_id=int(row["incident_id"]),
                    event_type=str(row["event_type"]),
                    details=str(row["details"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ]

    def append_event(self, *, incident_id: int, event_type: str, details: str) -> None:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            self._add_event(
                connection,
                incident_id=incident_id,
                event_type=event_type,
                details=details,
                timestamp=_now().isoformat(),
            )

    def _transition(self, *, incident_id: int, event_type: str) -> Incident:
        init_schema(self.db_path)
        now = _now().isoformat()
        with _connect(self.db_path) as connection:
            if event_type == "read":
                connection.execute(
                    "UPDATE incidents SET is_read = 1 WHERE id = ?",
                    (incident_id,),
                )
                details = "Incident marked as read"
            elif event_type == "acknowledged":
                connection.execute(
                    "UPDATE incidents SET acknowledged_at = ?, is_read = 1 WHERE id = ?",
                    (now, incident_id),
                )
                details = "Incident acknowledged by operator"
            else:
                connection.execute(
                    """
                    UPDATE incidents
                    SET status = 'resolved', resolved_at = ?, is_read = 1
                    WHERE id = ?
                    """,
                    (now, incident_id),
                )
                details = "Incident resolved by operator"

            self._add_event(
                connection,
                incident_id=incident_id,
                event_type=event_type,
                details=details,
                timestamp=now,
            )
            row = connection.execute(
                """
                SELECT id, fingerprint, severity, summary, status, is_read, occurrence_count,
                       first_seen_at, last_seen_at, acknowledged_at, resolved_at
                FROM incidents
                WHERE id = ?
                """,
                (incident_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_incident(row)

    @staticmethod
    def _add_event(
        connection: sqlite3.Connection,
        *,
        incident_id: int,
        event_type: str,
        details: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO incident_events (incident_id, event_type, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (incident_id, event_type, details, timestamp),
        )

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            id=int(row["id"]),
            fingerprint=str(row["fingerprint"]),
            severity=str(row["severity"]),
            summary=str(row["summary"]),
            status=str(row["status"]),
            is_read=bool(row["is_read"]),
            occurrence_count=int(row["occurrence_count"]),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
            acknowledged_at=str(row["acknowledged_at"]) if row["acknowledged_at"] else None,
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
        )
