"""Unit coverage for :class:`ProjectServiceRepository` (Epic 12, story 12.01)."""

from __future__ import annotations

import asyncio

import pytest

from services.api.app.calendar.project_services_repository import (
    ProjectService,
    ProjectServiceNotFound,
    ProjectServiceRepository,
    acquire_service_upsert_lock,
)


def _repo(tmp_path) -> ProjectServiceRepository:
    return ProjectServiceRepository(db_path=str(tmp_path / "calendar.sqlite3"))


def test_list_for_project_empty(tmp_path):
    repo = _repo(tmp_path)
    assert repo.list_for_project(project_id=1) == []


def test_upsert_insert_then_update_same_id(tmp_path, caplog):
    repo = _repo(tmp_path)
    inserted = repo.upsert(
        project_id=11,
        name="маникюр",
        description="классический",
        price_text="от 2000 ₽",
        tags=["nails"],
        duration_minutes=60,
        working_hours={"sat": [["10:00", "19:00"]]},
        service_days=["sat"],
        date_exceptions=["2026-01-07"],
    )
    assert isinstance(inserted, ProjectService)
    assert inserted.id > 0
    assert inserted.name == "маникюр"
    assert inserted.description == "классический"
    assert inserted.price_text == "от 2000 ₽"
    assert inserted.tags == ["nails"]
    assert inserted.duration_minutes == 60
    assert inserted.working_hours == {"sat": [["10:00", "19:00"]]}
    assert inserted.service_days == ["sat"]
    assert inserted.date_exceptions == ["2026-01-07"]
    assert inserted.updated_at is not None

    caplog.clear()
    with caplog.at_level("INFO", logger="services.api.app.calendar.project_services_repository"):
        updated = repo.upsert(
            project_id=11,
            name="маникюр",  # same lower(name)
            description="аппаратный",
            duration_minutes=75,
        )
    assert updated.id == inserted.id
    assert updated.description == "аппаратный"
    assert updated.duration_minutes == 75
    # JSON fields cleared on update because the caller did not pass them.
    assert updated.tags is None
    assert updated.working_hours is None
    assert updated.service_days is None
    assert updated.date_exceptions is None
    events = [r.message for r in caplog.records]
    assert "services_upsert_duplicate_name" in events


def test_upsert_case_insensitive_collision(tmp_path):
    repo = _repo(tmp_path)
    first = repo.upsert(project_id=1, name="Маникюр")
    second = repo.upsert(project_id=1, name="МАНИКЮР")  # same casefolded
    assert first.id == second.id
    assert repo.list_for_project(project_id=1)[-1].name == "МАНИКЮР"


def test_upsert_isolated_per_project(tmp_path):
    repo = _repo(tmp_path)
    a = repo.upsert(project_id=1, name="маникюр")
    b = repo.upsert(project_id=2, name="маникюр")
    # Same name under DIFFERENT project_id → DIFFERENT rows.
    assert a.id != b.id


def test_get_returns_row_or_raises(tmp_path):
    repo = _repo(tmp_path)
    inserted = repo.upsert(project_id=5, name="hair")
    fetched = repo.get(project_id=5, service_id=inserted.id)
    assert fetched.id == inserted.id
    with pytest.raises(ProjectServiceNotFound):
        repo.get(project_id=5, service_id=9999)


def test_get_by_name_case_insensitive(tmp_path):
    repo = _repo(tmp_path)
    inserted = repo.upsert(project_id=3, name="Маникюр")
    by_name = repo.get_by_name(project_id=3, name="МАНИКЮР")
    assert by_name is not None
    assert by_name.id == inserted.id
    assert repo.get_by_name(project_id=3, name="missing") is None


def test_list_calendar_eligible_filters_duration_null(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert(project_id=7, name="консультация")  # no duration_minutes
    eligible = repo.upsert(project_id=7, name="стрижка", duration_minutes=30)
    all_rows = repo.list_for_project(project_id=7)
    assert len(all_rows) == 2
    calendar_rows = repo.list_calendar_eligible(project_id=7)
    assert len(calendar_rows) == 1
    assert calendar_rows[0].id == eligible.id


def test_delete_removes_and_raises_when_missing(tmp_path):
    repo = _repo(tmp_path)
    inserted = repo.upsert(project_id=8, name="x")
    repo.delete(project_id=8, service_id=inserted.id)
    assert repo.list_for_project(project_id=8) == []
    with pytest.raises(ProjectServiceNotFound):
        repo.delete(project_id=8, service_id=inserted.id)


def test_acquire_service_upsert_lock_keys_by_casefolded_name(tmp_path):
    async def _run() -> None:
        a = await acquire_service_upsert_lock(project_id=1, name="Маникюр")
        b = await acquire_service_upsert_lock(project_id=1, name="маникюр")
        c = await acquire_service_upsert_lock(project_id=2, name="маникюр")
        assert a is b
        assert a is not c

    asyncio.run(_run())


def test_repository_constructor_runs_migration(tmp_path):
    """Constructing the repository against a fresh DB creates the table."""
    import sqlite3 as _sqlite

    path = str(tmp_path / "calendar.sqlite3")
    ProjectServiceRepository(db_path=path)
    with _sqlite.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "project_services" in names


def test_unicode_lower_handles_none():
    """The Unicode-aware ``lower`` UDF must be NULL-safe (SQLite invariant)."""
    from services.api.app.calendar.project_services_repository import _unicode_lower

    assert _unicode_lower(None) is None
    assert _unicode_lower("Привет") == "привет"
