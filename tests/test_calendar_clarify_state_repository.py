from __future__ import annotations

from pathlib import Path

import pytest

from services.api.app.calendar.clarify_state_repository import (
    CalendarClarifyStateRepository,
)


@pytest.fixture
def repo(tmp_path: Path) -> CalendarClarifyStateRepository:
    return CalendarClarifyStateRepository(db_path=str(tmp_path / "clarify.sqlite3"))


def test_unarmed_chat_reports_false(repo: CalendarClarifyStateRepository) -> None:
    assert repo.is_armed(42) is False


def test_arm_then_is_armed(repo: CalendarClarifyStateRepository) -> None:
    repo.arm(42, trace_id="t-1")
    assert repo.is_armed(42) is True
    # A different chat is unaffected.
    assert repo.is_armed(99) is False


def test_arm_is_idempotent_upsert(repo: CalendarClarifyStateRepository) -> None:
    repo.arm(42, trace_id="t-1")
    repo.arm(42, trace_id="t-2")
    assert repo.is_armed(42) is True


def test_clear_drops_flag(repo: CalendarClarifyStateRepository) -> None:
    repo.arm(42, trace_id="t-1")
    repo.clear(42)
    assert repo.is_armed(42) is False


def test_clear_when_absent_is_noop(repo: CalendarClarifyStateRepository) -> None:
    repo.clear(123)
    assert repo.is_armed(123) is False


def test_init_schema_idempotent(tmp_path: Path) -> None:
    db = str(tmp_path / "clarify.sqlite3")
    first = CalendarClarifyStateRepository(db_path=db)
    first.arm(7, trace_id="t")
    # Re-opening the same DB must not wipe or error.
    second = CalendarClarifyStateRepository(db_path=db)
    assert second.is_armed(7) is True
