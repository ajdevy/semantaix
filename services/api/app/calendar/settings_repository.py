"""Per-project calendar settings + service rules (Epic 11, story 11.01).

Owns ``calendar_project_settings`` and ``calendar_service_rules``. Calendar is
**opt-in per project**: a project with no settings row reads as disabled, and
``is_enabled`` is the cheap gate the answerer hits FIRST before any intent
detection or API call. Sync ``sqlite3`` per project-context; callers dispatch
via ``asyncio.to_thread``. No raw SQL lives outside this class.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_TIMEZONE = "Europe/Moscow"
_DEFAULT_LOOKAHEAD_DAYS = 60


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CalendarProjectSettings:
    project_id: int
    enabled: bool
    calendar_operator: str | None
    project_timezone: str
    lookahead_days: int
    updated_at: str | None


@dataclass(frozen=True)
class ServiceRule:
    id: int
    project_id: int
    name: str | None
    duration_minutes: int | None
    working_hours: dict | None
    service_days: list | None
    date_exceptions: list | None
    updated_at: str | None


def _loads(value: str | None):
    if value is None:
        return None
    return json.loads(value)


class CalendarSettingsRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_project_settings (
                    project_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    calendar_operator TEXT,
                    project_timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    lookahead_days INTEGER NOT NULL DEFAULT 60,
                    updated_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_service_rules (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER,
                    name TEXT,
                    duration_minutes INTEGER,
                    working_hours_json TEXT,
                    service_days_json TEXT,
                    date_exceptions_json TEXT,
                    updated_at TEXT
                )
                """
            )

    def _row_to_settings(self, row: sqlite3.Row) -> CalendarProjectSettings:
        return CalendarProjectSettings(
            project_id=int(row["project_id"]),
            enabled=bool(row["enabled"]),
            calendar_operator=row["calendar_operator"],
            project_timezone=str(row["project_timezone"]),
            lookahead_days=int(row["lookahead_days"]),
            updated_at=row["updated_at"],
        )

    def get(self, project_id: int) -> CalendarProjectSettings | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT project_id, enabled, calendar_operator,
                       project_timezone, lookahead_days, updated_at
                FROM calendar_project_settings
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_settings(row)

    def is_enabled(self, project_id: int) -> bool:
        """Cheap default-off gate: no row (or enabled=0) ⇒ disabled."""
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT enabled FROM calendar_project_settings WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return False
        return bool(row["enabled"])

    def enable(
        self,
        project_id: int,
        *,
        calendar_operator: str | None = None,
        project_timezone: str = _DEFAULT_TIMEZONE,
        lookahead_days: int = _DEFAULT_LOOKAHEAD_DAYS,
    ) -> None:
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_project_settings
                    (project_id, enabled, calendar_operator,
                     project_timezone, lookahead_days, updated_at)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    enabled = 1,
                    calendar_operator = excluded.calendar_operator,
                    project_timezone = excluded.project_timezone,
                    lookahead_days = excluded.lookahead_days,
                    updated_at = excluded.updated_at
                """,
                (project_id, calendar_operator, project_timezone, lookahead_days, now),
            )

    def disable(self, project_id: int) -> None:
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_project_settings
                    (project_id, enabled, updated_at)
                VALUES (?, 0, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    enabled = 0,
                    updated_at = excluded.updated_at
                """,
                (project_id, now),
            )

    def set_calendar_operator(self, project_id: int, *, calendar_operator: str | None) -> None:
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO calendar_project_settings
                    (project_id, calendar_operator, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    calendar_operator = excluded.calendar_operator,
                    updated_at = excluded.updated_at
                """,
                (project_id, calendar_operator, now),
            )

    def _row_to_rule(self, row: sqlite3.Row) -> ServiceRule:
        return ServiceRule(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            name=row["name"],
            duration_minutes=row["duration_minutes"],
            working_hours=_loads(row["working_hours_json"]),
            service_days=_loads(row["service_days_json"]),
            date_exceptions=_loads(row["date_exceptions_json"]),
            updated_at=row["updated_at"],
        )

    def list_service_rules(self, project_id: int) -> list[ServiceRule]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, name, duration_minutes,
                       working_hours_json, service_days_json,
                       date_exceptions_json, updated_at
                FROM calendar_service_rules
                WHERE project_id = ?
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def upsert_service_rule(
        self,
        *,
        project_id: int,
        name: str | None = None,
        duration_minutes: int | None = None,
        working_hours: dict | None = None,
        service_days: list | None = None,
        date_exceptions: list | None = None,
        rule_id: int | None = None,
    ) -> int:
        now = _now()
        working_hours_json = None if working_hours is None else json.dumps(working_hours)
        service_days_json = None if service_days is None else json.dumps(service_days)
        date_exceptions_json = (
            None if date_exceptions is None else json.dumps(date_exceptions)
        )
        with _connect(self.db_path) as connection:
            if rule_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO calendar_service_rules
                        (project_id, name, duration_minutes, working_hours_json,
                         service_days_json, date_exceptions_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        name,
                        duration_minutes,
                        working_hours_json,
                        service_days_json,
                        date_exceptions_json,
                        now,
                    ),
                )
                return int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE calendar_service_rules
                SET project_id = ?, name = ?, duration_minutes = ?,
                    working_hours_json = ?, service_days_json = ?,
                    date_exceptions_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    project_id,
                    name,
                    duration_minutes,
                    working_hours_json,
                    service_days_json,
                    date_exceptions_json,
                    now,
                    rule_id,
                ),
            )
            return rule_id

    def delete_service_rule(self, rule_id: int) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM calendar_service_rules WHERE id = ?",
                (rule_id,),
            )
