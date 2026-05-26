"""Alias-delegation tests for the deprecated
``POST/DELETE /calendar/projects/{id}/services`` endpoints (Epic 13, story
13.02).

The Epic-11 contract (status codes, response shapes, ``rule_id``-keyed updates)
stays exactly as-is so existing automation keeps working — the only behavior
change is a new ``deprecation_warning_calendar_services_endpoint`` structured
log line on every call. Functional parity with the canonical surface is also
asserted: a row inserted via the deprecated path is visible via the canonical
``GET /api/projects/{id}/services``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.calendar.project_services_repository import (
    ProjectServiceRepository,
)
from services.api.app.calendar.settings_repository import CalendarSettingsRepository
from services.api.app.main import app as api_app

_INTERNAL_TOKEN = "test-internal-token"
_AUTH = {"Authorization": f"Bearer {_INTERNAL_TOKEN}"}
_PROJECT_ID = 73
_OPERATOR = "@op"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    calendar_db = str(tmp_path / "calendar.sqlite3")
    settings_repo = CalendarSettingsRepository(db_path=calendar_db)
    services_repo = ProjectServiceRepository(db_path=calendar_db)
    monkeypatch.setattr(api_main.settings, "internal_service_token", _INTERNAL_TOKEN)
    monkeypatch.setattr(api_main, "calendar_settings_repository", settings_repo)
    monkeypatch.setattr(api_main, "project_services_repository", services_repo)
    client = TestClient(api_app)
    yield {"client": client, "settings_repo": settings_repo, "services_repo": services_repo}


def _deprecation_records(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.message == "deprecation_warning_calendar_services_endpoint"
    ]


def test_alias_post_emits_deprecation_and_persists_row(env, caplog):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    caplog.set_level(logging.INFO, logger="services.api.app.main")
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "name": "маникюр",
            "duration_minutes": 60,
        },
    )
    assert resp.status_code == 200
    rule_id = resp.json()["id"]
    records = _deprecation_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.endpoint == "POST /calendar/projects/{project_id}/services"
    assert record.project_id == _PROJECT_ID
    assert record.actor_role == "operator"
    assert record.canonical_endpoint == "POST /api/projects/{project_id}/services"
    # Functional parity: same row visible via the canonical GET.
    listing = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    ).json()
    assert [s["id"] for s in listing["services"]] == [rule_id]
    assert listing["services"][0]["name"] == "маникюр"


def test_alias_delete_emits_deprecation_and_removes_row(env, caplog):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    created = env["services_repo"].upsert(project_id=_PROJECT_ID, name="x")
    caplog.set_level(logging.INFO, logger="services.api.app.main")
    resp = env["client"].request(
        "DELETE",
        f"/calendar/projects/{_PROJECT_ID}/services/{created.id}",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    records = _deprecation_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.endpoint == "DELETE /calendar/projects/{project_id}/services/{rule_id}"
    assert record.project_id == _PROJECT_ID
    assert record.actor_role == "operator"
    assert record.canonical_endpoint == (
        "DELETE /api/projects/{project_id}/services/{service_id}"
    )
    # Functional parity: row removed in the underlying project_services table.
    assert env["services_repo"].list_for_project(project_id=_PROJECT_ID) == []


def test_alias_post_admin_still_allowed_for_back_compat(env, caplog):
    """Epic-11 callers (admins doing add/edit) keep working — the alias's
    looser ``authorize_calendar_config`` gate is intentionally preserved."""
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    caplog.set_level(logging.INFO, logger="services.api.app.main")
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": "@admin", "actor_role": "admin", "name": "y"},
    )
    assert resp.status_code == 200
    records = _deprecation_records(caplog)
    assert len(records) == 1
    assert records[0].actor_role == "admin"
