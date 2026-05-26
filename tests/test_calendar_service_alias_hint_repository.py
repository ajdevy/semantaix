"""Unit coverage for the `/calendar_service` migration-hint dedup table
(Epic 13, story 13.03).

The table lives in ``.data/semantaix_nl_ops.db`` and keys on
``(project_id, operator)``; first call returns True (caller should DM), every
subsequent call for the same key returns False (no DM). The schema bootstrap
is idempotent — second run is a no-op.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.api.app.calendar.calendar_service_alias_hint_repository import (
    init_calendar_service_alias_hint_schema,
    should_send_calendar_service_alias_hint,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "nl_ops.db")
    init_calendar_service_alias_hint_schema(path)
    return path


def _table_exists(db_path: str) -> bool:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='calendar_service_alias_hint_sent'"
        ).fetchone()
    return row is not None


def test_schema_init_creates_table(db_path: str) -> None:
    assert _table_exists(db_path)


def test_schema_init_is_idempotent(db_path: str) -> None:
    # Second call must be a no-op (no IntegrityError, table still there).
    init_calendar_service_alias_hint_schema(db_path)
    init_calendar_service_alias_hint_schema(db_path)
    assert _table_exists(db_path)


def test_should_send_first_call_returns_true(db_path: str) -> None:
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    assert (
        should_send_calendar_service_alias_hint(
            db_path=db_path, project_id=11, operator="@op", now=now
        )
        is True
    )


def test_should_send_second_call_returns_false(db_path: str) -> None:
    now1 = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    now2 = datetime(2026, 5, 24, 12, 5, 0, tzinfo=UTC)
    assert (
        should_send_calendar_service_alias_hint(
            db_path=db_path, project_id=11, operator="@op", now=now1
        )
        is True
    )
    assert (
        should_send_calendar_service_alias_hint(
            db_path=db_path, project_id=11, operator="@op", now=now2
        )
        is False
    )


def test_should_send_different_operator_returns_true(db_path: str) -> None:
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    should_send_calendar_service_alias_hint(
        db_path=db_path, project_id=11, operator="@one", now=now
    )
    # Different operator on same project gets the hint independently.
    assert (
        should_send_calendar_service_alias_hint(
            db_path=db_path, project_id=11, operator="@two", now=now
        )
        is True
    )


def test_should_send_different_project_returns_true(db_path: str) -> None:
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    should_send_calendar_service_alias_hint(
        db_path=db_path, project_id=11, operator="@op", now=now
    )
    # Same operator on a different project also gets the hint.
    assert (
        should_send_calendar_service_alias_hint(
            db_path=db_path, project_id=22, operator="@op", now=now
        )
        is True
    )


def test_sequential_concurrent_calls_dedup_via_insert_or_ignore(
    db_path: str,
) -> None:
    # Sequential simulation: an interleaved double-fire would still produce
    # exactly one INSERT due to INSERT OR IGNORE on the PRIMARY KEY.
    now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    first = should_send_calendar_service_alias_hint(
        db_path=db_path, project_id=11, operator="@op", now=now
    )
    second = should_send_calendar_service_alias_hint(
        db_path=db_path, project_id=11, operator="@op", now=now
    )
    third = should_send_calendar_service_alias_hint(
        db_path=db_path, project_id=11, operator="@op", now=now
    )
    assert (first, second, third) == (True, False, False)
    # Confirm exactly one row was persisted.
    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM calendar_service_alias_hint_sent"
        ).fetchone()[0]
    assert count == 1
