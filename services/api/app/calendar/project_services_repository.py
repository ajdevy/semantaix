"""Canonical project services repository (Epic 13, story 13.01).

Owns the ``project_services`` table in ``.data/semantaix_calendar.db``. This
table is the **one** structured catalog of operator-curated services per
project, powering both the catalog answer (FR-23) and the calendar
availability path (rows with ``duration_minutes IS NOT NULL`` only).

The table is the renamed Epic-11 ``calendar_service_rules`` plus three catalog
columns (``description``, ``price_text``, ``tags_json``) and a
``UNIQUE(project_id, lower(name))`` constraint enforced via a unique index on
the same expression.

Migration semantics (``run_project_services_migration``):
- If ``calendar_service_rules`` exists and ``project_services`` does not →
  rename in place (preserves rows + their original ``id`` values).
- Else if neither exists (fresh deploy) → create ``project_services`` with the
  final schema directly, no Epic 11 prerequisite required.
- For every new column, ``PRAGMA table_info`` is consulted and the column is
  added only when absent — second run is a no-op.
- Unique + lookup indexes always created ``IF NOT EXISTS``.

Sync ``sqlite3`` per project-context; callers dispatch via
``asyncio.to_thread``. The single-flight ``asyncio.Lock`` is held by the
**caller** (api endpoint / NL ops handler) around the dispatch, mirroring the
calendar token-refresh lock pattern (`access_token_cache.AccessTokenProvider`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectServiceNotFound(Exception):
    """Raised when a target row does not exist for ``get``/``delete``."""


@dataclass(frozen=True)
class ProjectService:
    id: int
    project_id: int
    name: str
    description: str | None
    price_text: str | None
    tags: list | None
    duration_minutes: int | None
    working_hours: dict | None
    service_days: list | None
    date_exceptions: list | None
    updated_at: str | None


def _unicode_lower(value):
    """SQLite UDF replacement for ``lower`` that handles non-ASCII (Cyrillic)."""
    if value is None:
        return None
    return str(value).casefold()


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    # SQLite's built-in ``lower`` is ASCII-only; register a Unicode-aware
    # replacement so the UNIQUE expression index and case-insensitive lookups
    # work for Cyrillic service names (FR-23: Russian-first).
    connection.create_function("lower", 1, _unicode_lower, deterministic=True)
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: str | None):
    if value is None:
        return None
    return json.loads(value)


def _dumps(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


_FINAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("description", "TEXT"),
    ("price_text", "TEXT"),
    ("tags_json", "TEXT"),
)


def run_project_services_migration(db_path: str) -> None:
    """Idempotent rename + additive column migration for ``project_services``.

    Safe to call on every container boot. See module docstring for the three
    bootstrap modes (fresh / migrated-from-Epic-11 / re-run no-op).
    """
    with _connect(db_path) as connection:
        existing = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name IN ('calendar_service_rules', 'project_services')"
            ).fetchall()
        }
        if "project_services" not in existing:
            if "calendar_service_rules" in existing:
                connection.execute(
                    "ALTER TABLE calendar_service_rules RENAME TO project_services"
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE project_services (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        price_text TEXT,
                        tags_json TEXT,
                        duration_minutes INTEGER,
                        working_hours_json TEXT,
                        service_days_json TEXT,
                        date_exceptions_json TEXT,
                        updated_at TEXT
                    )
                    """
                )
        existing_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(project_services)"
            ).fetchall()
        }
        for column_name, column_type in _FINAL_COLUMNS:
            if column_name not in existing_columns:
                connection.execute(
                    f"ALTER TABLE project_services ADD COLUMN {column_name} {column_type}"
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS project_services_unique_name "
            "ON project_services(project_id, lower(name))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS project_services_project_idx "
            "ON project_services(project_id)"
        )


# --- Per-(project_id, lower(name)) asyncio.Lock factory -----------------------
_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_LOCKS_GUARD: asyncio.Lock | None = None


def _locks_guard() -> asyncio.Lock:
    """Lazy-create the guard inside the running event loop."""
    global _LOCKS_GUARD
    if _LOCKS_GUARD is None:
        _LOCKS_GUARD = asyncio.Lock()
    return _LOCKS_GUARD


def _lock_key(*, project_id: int, name: str) -> tuple[int, str]:
    # name.casefold() is Russian-safe; matches the unique-index expression.
    return (project_id, name.casefold())


async def acquire_service_upsert_lock(
    *, project_id: int, name: str
) -> asyncio.Lock:
    """Return the per-``(project_id, lower(name))`` lock for single-flight upsert.

    The caller awaits this then ``async with`` the returned lock; the actual
    sqlite write goes through ``asyncio.to_thread``. Mirrors the per-operator
    pattern in :class:`AccessTokenProvider`.
    """
    key = _lock_key(project_id=project_id, name=name)
    async with _locks_guard():
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


class ProjectServiceRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        run_project_services_migration(db_path)

    def _row_to_service(self, row: sqlite3.Row) -> ProjectService:
        return ProjectService(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            name=str(row["name"]),
            description=row["description"],
            price_text=row["price_text"],
            tags=_loads(row["tags_json"]),
            duration_minutes=row["duration_minutes"],
            working_hours=_loads(row["working_hours_json"]),
            service_days=_loads(row["service_days_json"]),
            date_exceptions=_loads(row["date_exceptions_json"]),
            updated_at=row["updated_at"],
        )

    def list_for_project(self, *, project_id: int) -> list[ProjectService]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, name, description, price_text, tags_json,
                       duration_minutes, working_hours_json, service_days_json,
                       date_exceptions_json, updated_at
                FROM project_services
                WHERE project_id = ?
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_service(row) for row in rows]

    def list_calendar_eligible(self, *, project_id: int) -> list[ProjectService]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, name, description, price_text, tags_json,
                       duration_minutes, working_hours_json, service_days_json,
                       date_exceptions_json, updated_at
                FROM project_services
                WHERE project_id = ? AND duration_minutes IS NOT NULL
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_service(row) for row in rows]

    def get_by_name(
        self, *, project_id: int, name: str
    ) -> ProjectService | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, project_id, name, description, price_text, tags_json,
                       duration_minutes, working_hours_json, service_days_json,
                       date_exceptions_json, updated_at
                FROM project_services
                WHERE project_id = ? AND lower(name) = lower(?)
                """,
                (project_id, name),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_service(row)

    def get(self, *, project_id: int, service_id: int) -> ProjectService:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, project_id, name, description, price_text, tags_json,
                       duration_minutes, working_hours_json, service_days_json,
                       date_exceptions_json, updated_at
                FROM project_services
                WHERE project_id = ? AND id = ?
                """,
                (project_id, service_id),
            ).fetchone()
        if row is None:
            raise ProjectServiceNotFound(
                f"project_service_not_found:{project_id}:{service_id}"
            )
        return self._row_to_service(row)

    def upsert(
        self,
        *,
        project_id: int,
        name: str,
        description: str | None = None,
        price_text: str | None = None,
        tags: list | None = None,
        duration_minutes: int | None = None,
        working_hours: dict | None = None,
        service_days: list | None = None,
        date_exceptions: list | None = None,
    ) -> ProjectService:
        """Insert or update keyed on ``(project_id, lower(name))``.

        Existing-name insert is converted to an UPDATE inside a single
        ``BEGIN IMMEDIATE`` transaction. ``services_upsert_duplicate_name``
        structured log is emitted when an UPDATE was triggered (alias-path
        races are still possible since the unique-index expression cannot be
        used in ``ON CONFLICT`` — we explicitly SELECT-then-INSERT-or-UPDATE).
        """
        now = _now()
        tags_json = _dumps(tags)
        working_hours_json = _dumps(working_hours)
        service_days_json = _dumps(service_days)
        date_exceptions_json = _dumps(date_exceptions)
        with _connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM project_services WHERE project_id = ? "
                "AND lower(name) = lower(?)",
                (project_id, name),
            ).fetchone()
            if existing is not None:
                service_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE project_services
                    SET name = ?, description = ?, price_text = ?, tags_json = ?,
                        duration_minutes = ?, working_hours_json = ?,
                        service_days_json = ?, date_exceptions_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        description,
                        price_text,
                        tags_json,
                        duration_minutes,
                        working_hours_json,
                        service_days_json,
                        date_exceptions_json,
                        now,
                        service_id,
                    ),
                )
                logger.info(
                    "services_upsert_duplicate_name",
                    extra={
                        "project_id": project_id,
                        "service_id": service_id,
                        "service_name": name,
                    },
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO project_services
                        (project_id, name, description, price_text, tags_json,
                         duration_minutes, working_hours_json,
                         service_days_json, date_exceptions_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        name,
                        description,
                        price_text,
                        tags_json,
                        duration_minutes,
                        working_hours_json,
                        service_days_json,
                        date_exceptions_json,
                        now,
                    ),
                )
                service_id = int(cursor.lastrowid)
        return self.get(project_id=project_id, service_id=service_id)

    def delete(self, *, project_id: int, service_id: int) -> None:
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                "DELETE FROM project_services WHERE project_id = ? AND id = ?",
                (project_id, service_id),
            )
            if cursor.rowcount == 0:
                raise ProjectServiceNotFound(
                    f"project_service_not_found:{project_id}:{service_id}"
                )
