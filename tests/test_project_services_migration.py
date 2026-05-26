"""Unit coverage for the project_services migration (Epic 13, story 13.01).

Three bootstrap modes are exercised:
 1. fresh-deploy CREATE path (no calendar tables at all)
 2. Epic-11 → Epic-13 RENAME path (calendar_service_rules exists, has rows)
 3. re-run no-op (second invocation must be a no-op)
"""

from __future__ import annotations

import sqlite3

from services.api.app.calendar.project_services_repository import (
    run_project_services_migration,
)


def _table_columns(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }


def _table_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }


def _index_names(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return {
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA index_list({table})"
            ).fetchall()
        }


def test_fresh_deploy_creates_table_directly(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    run_project_services_migration(path)
    tables = _table_names(path)
    assert "project_services" in tables
    assert "calendar_service_rules" not in tables
    # Other Epic-11 tables NOT created by this migration.
    assert "calendar_project_settings" not in tables
    assert "calendar_operator_tokens" not in tables
    assert "calendar_oauth_pending_state" not in tables

    cols = _table_columns(path, "project_services")
    expected = {
        "id",
        "project_id",
        "name",
        "description",
        "price_text",
        "tags_json",
        "duration_minutes",
        "working_hours_json",
        "service_days_json",
        "date_exceptions_json",
        "updated_at",
    }
    assert expected == cols


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    run_project_services_migration(path)
    cols_first = _table_columns(path, "project_services")
    indexes_first = _index_names(path, "project_services")
    run_project_services_migration(path)
    cols_second = _table_columns(path, "project_services")
    indexes_second = _index_names(path, "project_services")
    assert cols_first == cols_second
    assert indexes_first == indexes_second


def test_rename_path_preserves_rows(tmp_path):
    """An Epic-11 db with calendar_service_rules + row → renamed + new cols added."""
    path = str(tmp_path / "calendar.sqlite3")
    # Simulate Epic-11 schema: calendar_service_rules with old columns.
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE calendar_service_rules (
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
        connection.execute(
            """
            INSERT INTO calendar_service_rules
              (project_id, name, duration_minutes, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (42, "preserved", 30, "2026-01-01T00:00:00+00:00"),
        )

    run_project_services_migration(path)

    tables = _table_names(path)
    assert "project_services" in tables
    assert "calendar_service_rules" not in tables

    cols = _table_columns(path, "project_services")
    assert {"description", "price_text", "tags_json"}.issubset(cols)

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT project_id, name, duration_minutes, description, price_text, tags_json "
            "FROM project_services WHERE name = ?",
            ("preserved",),
        ).fetchone()
    assert row is not None
    assert row["project_id"] == 42
    assert row["duration_minutes"] == 30
    # New columns are NULL on the migrated row.
    assert row["description"] is None
    assert row["price_text"] is None
    assert row["tags_json"] is None


def test_unique_index_on_project_id_and_lower_name(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    run_project_services_migration(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "PRAGMA index_list(project_services)"
        ).fetchall()
        index_names = [str(r["name"]) for r in rows]
        assert "project_services_unique_name" in index_names
        unique_flags = {
            str(r["name"]): int(r["unique"])
            for r in rows
        }
        assert unique_flags["project_services_unique_name"] == 1
        assert "project_services_project_idx" in index_names


def test_migration_touch_isolation_other_tables(tmp_path):
    """Other Epic-11 tables + rows are not modified by this migration."""
    path = str(tmp_path / "calendar.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE calendar_project_settings (
                project_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "INSERT INTO calendar_project_settings (project_id, enabled) VALUES (1, 1)"
        )

    cols_before = _table_columns(path, "calendar_project_settings")
    with sqlite3.connect(path) as connection:
        rows_before = connection.execute(
            "SELECT * FROM calendar_project_settings"
        ).fetchall()

    run_project_services_migration(path)

    cols_after = _table_columns(path, "calendar_project_settings")
    with sqlite3.connect(path) as connection:
        rows_after = connection.execute(
            "SELECT * FROM calendar_project_settings"
        ).fetchall()
    assert cols_before == cols_after
    assert rows_before == rows_after


def test_partial_existing_columns_get_added(tmp_path):
    """If project_services exists without new columns, additive ALTERs run."""
    path = str(tmp_path / "calendar.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE project_services (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                duration_minutes INTEGER,
                working_hours_json TEXT,
                service_days_json TEXT,
                date_exceptions_json TEXT,
                updated_at TEXT
            )
            """
        )
    run_project_services_migration(path)
    cols = _table_columns(path, "project_services")
    assert {"description", "price_text", "tags_json"}.issubset(cols)
