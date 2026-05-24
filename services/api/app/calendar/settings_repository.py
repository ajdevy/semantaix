"""Per-project calendar settings (Epic 11, story 11.01).

Owns ``calendar_project_settings``. Calendar is **opt-in per project**: a
project with no settings row reads as disabled, and ``is_enabled`` is the
cheap gate the answerer hits FIRST before any intent detection or API call.
Sync ``sqlite3`` per project-context; callers dispatch via
``asyncio.to_thread``. No raw SQL lives outside this class.

Epic 12 (story 12.01) — the former ``calendar_service_rules`` table is now
``project_services`` (in the same DB), owned by
:class:`ProjectServiceRepository`. The legacy service-rule methods on this
class (``list_service_rules`` / ``upsert_service_rule`` /
``delete_service_rule``) are 60-day-deprecated delegating aliases that emit
:class:`DeprecationWarning`; they exist only so Epic-11 callers keep working
until the Epic-13 cleanup PR.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .project_services_repository import (
    ProjectService,
    ProjectServiceRepository,
    _unicode_lower,
    run_project_services_migration,
)

_DEFAULT_TIMEZONE = "Europe/Moscow"
_DEFAULT_LOOKAHEAD_DAYS = 60

_DEPRECATION_MESSAGE = (
    "CalendarSettingsRepository service-rule methods are deprecated; "
    "use ProjectServiceRepository directly (Epic 12, story 12.01)."
)
_DEPRECATION_EVENT = "deprecation_warning_calendar_settings_service_rule"


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    # Match project_services_repository's Unicode-aware ``lower`` UDF so
    # writes/reads on the same row stay consistent across both repos.
    connection.create_function("lower", 1, _unicode_lower, deterministic=True)
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
    """Legacy 8-field projection of :class:`ProjectService`.

    Preserved for Epic-11 callers that still import ``ServiceRule`` directly.
    Epic-12 callers should use :class:`ProjectService`. Removed in the Epic-13
    cleanup PR.
    """

    id: int
    project_id: int
    name: str | None
    duration_minutes: int | None
    working_hours: dict | None
    service_days: list | None
    date_exceptions: list | None
    updated_at: str | None


def _project_service_to_rule(service: ProjectService) -> ServiceRule:
    return ServiceRule(
        id=service.id,
        project_id=service.project_id,
        name=service.name,
        duration_minutes=service.duration_minutes,
        working_hours=service.working_hours,
        service_days=service.service_days,
        date_exceptions=service.date_exceptions,
        updated_at=service.updated_at,
    )


_logger = logging.getLogger(__name__)


def _warn_deprecated(*, project_id: int | None = None) -> None:
    warnings.warn(
        _DEPRECATION_MESSAGE,
        DeprecationWarning,
        stacklevel=3,
    )
    extra: dict = {"event": _DEPRECATION_EVENT}
    if project_id is not None:
        extra["project_id"] = project_id
    _logger.info(_DEPRECATION_EVENT, extra=extra)


class CalendarSettingsRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()
        self._services = ProjectServiceRepository(db_path=db_path)

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
        # Epic 12: the project_services table replaces calendar_service_rules.
        # The migration is idempotent, so re-running on every constructor call
        # mirrors the existing init_schema() habit.
        run_project_services_migration(self.db_path)

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

    # --- Epic-12 deprecated aliases ----------------------------------------
    # These keep their Epic-11 signatures so the existing callers (api
    # endpoint, e2e tests, availability_answerer fakes) compile unchanged.
    # Removed in the Epic-13 cleanup PR.

    def list_service_rules(self, project_id: int) -> list[ServiceRule]:
        _warn_deprecated(project_id=project_id)
        rows = self._services.list_for_project(project_id=project_id)
        return [_project_service_to_rule(row) for row in rows]

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
        _warn_deprecated(project_id=project_id)
        # Epic 11 allowed name=None and updated by explicit rule_id. Epic 12's
        # canonical upsert is keyed on ``(project_id, lower(name))``; the
        # legacy by-id UPDATE path is bridged with a direct SQL update so the
        # caller-provided rule_id stays stable (existing tests assert this).
        now = _now()
        working_hours_json = (
            None if working_hours is None else json.dumps(working_hours, ensure_ascii=False)
        )
        service_days_json = (
            None if service_days is None else json.dumps(service_days, ensure_ascii=False)
        )
        date_exceptions_json = (
            None if date_exceptions is None else json.dumps(date_exceptions, ensure_ascii=False)
        )
        if rule_id is not None:
            with _connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE project_services
                    SET project_id = ?, name = ?, duration_minutes = ?,
                        working_hours_json = ?, service_days_json = ?,
                        date_exceptions_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        project_id,
                        "" if name is None else name,
                        duration_minutes,
                        working_hours_json,
                        service_days_json,
                        date_exceptions_json,
                        now,
                        rule_id,
                    ),
                )
            return rule_id
        # Fresh insert path — the legacy contract allowed ``name=None``, but
        # the new schema declares ``name`` NOT NULL. Persist an empty string
        # when callers omitted it (Epic-11 tests do this for "rule shell"
        # creation); the deprecated alias path will be retired in Epic 13.
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO project_services
                    (project_id, name, duration_minutes, working_hours_json,
                     service_days_json, date_exceptions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    "" if name is None else name,
                    duration_minutes,
                    working_hours_json,
                    service_days_json,
                    date_exceptions_json,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def delete_service_rule(self, rule_id: int) -> None:
        _warn_deprecated()
        # Epic 11 deleted by rule_id only; the legacy contract was idempotent
        # (no error on missing row), so we mirror that.
        with _connect(self.db_path) as connection:
            connection.execute(
                "DELETE FROM project_services WHERE id = ?",
                (rule_id,),
            )
