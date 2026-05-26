import sqlite3

import pytest

from services.api.app.calendar.settings_repository import (
    CalendarProjectSettings,
    CalendarSettingsRepository,
    ServiceRule,
)


def _repo(tmp_path) -> CalendarSettingsRepository:
    return CalendarSettingsRepository(db_path=str(tmp_path / "calendar.sqlite3"))


def test_init_schema_creates_tables(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    CalendarSettingsRepository(db_path=path)
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    # Epic 13 renamed calendar_service_rules → project_services.
    assert "calendar_project_settings" in names
    assert "project_services" in names
    assert "calendar_service_rules" not in names


def test_is_enabled_false_when_no_row(tmp_path):
    repo = _repo(tmp_path)
    assert repo.is_enabled(42) is False
    assert repo.get(42) is None


def test_enable_disable_round_trip(tmp_path):
    repo = _repo(tmp_path)
    repo.enable(
        7,
        calendar_operator="@op",
        project_timezone="Europe/Berlin",
        lookahead_days=30,
    )
    assert repo.is_enabled(7) is True
    settings = repo.get(7)
    assert isinstance(settings, CalendarProjectSettings)
    assert settings.project_id == 7
    assert settings.enabled is True
    assert settings.calendar_operator == "@op"
    assert settings.project_timezone == "Europe/Berlin"
    assert settings.lookahead_days == 30
    assert settings.updated_at is not None

    repo.disable(7)
    assert repo.is_enabled(7) is False
    disabled = repo.get(7)
    assert disabled.enabled is False


def test_enable_defaults(tmp_path):
    repo = _repo(tmp_path)
    repo.enable(1)
    settings = repo.get(1)
    assert settings.calendar_operator is None
    assert settings.project_timezone == "Europe/Moscow"
    assert settings.lookahead_days == 60


def test_disable_creates_row_when_absent(tmp_path):
    repo = _repo(tmp_path)
    repo.disable(99)
    settings = repo.get(99)
    assert settings is not None
    assert settings.enabled is False


def test_set_calendar_operator_inserts_then_updates(tmp_path):
    repo = _repo(tmp_path)
    repo.set_calendar_operator(3, calendar_operator="@first")
    assert repo.get(3).calendar_operator == "@first"
    repo.set_calendar_operator(3, calendar_operator="@second")
    assert repo.get(3).calendar_operator == "@second"


def test_service_rule_upsert_list_delete(tmp_path):
    repo = _repo(tmp_path)
    with pytest.warns(DeprecationWarning):
        assert repo.list_service_rules(5) == []

    with pytest.warns(DeprecationWarning):
        rule_id = repo.upsert_service_rule(
            project_id=5,
            name="haircut",
            duration_minutes=45,
            working_hours={"mon": ["09:00", "18:00"]},
            service_days=["mon", "tue"],
            date_exceptions=["2026-01-01"],
        )
    with pytest.warns(DeprecationWarning):
        rules = repo.list_service_rules(5)
    assert len(rules) == 1
    rule = rules[0]
    assert isinstance(rule, ServiceRule)
    assert rule.id == rule_id
    assert rule.name == "haircut"
    assert rule.duration_minutes == 45
    assert rule.working_hours == {"mon": ["09:00", "18:00"]}
    assert rule.service_days == ["mon", "tue"]
    assert rule.date_exceptions == ["2026-01-01"]
    assert rule.updated_at is not None

    with pytest.warns(DeprecationWarning):
        updated_id = repo.upsert_service_rule(
            project_id=5,
            name="haircut-deluxe",
            duration_minutes=60,
            rule_id=rule_id,
        )
    assert updated_id == rule_id
    with pytest.warns(DeprecationWarning):
        updated = repo.list_service_rules(5)[0]
    assert updated.name == "haircut-deluxe"
    assert updated.duration_minutes == 60
    assert updated.working_hours is None
    assert updated.service_days is None
    assert updated.date_exceptions is None

    with pytest.warns(DeprecationWarning):
        repo.delete_service_rule(rule_id)
    with pytest.warns(DeprecationWarning):
        assert repo.list_service_rules(5) == []


def test_init_schema_idempotent_preserves_rows(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    repo = CalendarSettingsRepository(db_path=path)
    repo.enable(11, calendar_operator="@keep")
    with pytest.warns(DeprecationWarning):
        rule_id = repo.upsert_service_rule(project_id=11, name="r")
    repo.init_schema()
    repo.init_schema()
    assert repo.is_enabled(11) is True
    assert repo.get(11).calendar_operator == "@keep"
    with pytest.warns(DeprecationWarning):
        rules = repo.list_service_rules(11)
    assert len(rules) == 1
    assert rules[0].id == rule_id


def test_delete_service_rule_idempotent_when_missing(tmp_path):
    """Legacy alias is idempotent — no error when the row is absent."""
    repo = _repo(tmp_path)
    with pytest.warns(DeprecationWarning):
        repo.delete_service_rule(9999)  # never inserted; must not raise
