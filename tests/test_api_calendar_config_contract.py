"""Contract tests for the calendar enable/disable + service config endpoints
(Epic 11, story 11.08).

Covers the operator-vs-admin permission model (FR-18/FR-21): operator and admin
both enable/disable + configure services; an admin attempting disconnect → 403;
disable keeps the stored token; malformed service rules are rejected.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.calendar.settings_repository import CalendarSettingsRepository
from services.api.app.calendar.token_repository import (
    CalendarTokenRepository,
    TokenNotFound,
)
from services.api.app.main import app as api_app

_INTERNAL_TOKEN = "test-internal-token"
_AUTH = {"Authorization": f"Bearer {_INTERNAL_TOKEN}"}
_PROJECT_ID = 7
_OPERATOR = "@op"
_OTHER_OPERATOR = "@other"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    calendar_db = str(tmp_path / "calendar.sqlite3")
    settings_repo = CalendarSettingsRepository(db_path=calendar_db)
    token_repo = CalendarTokenRepository(
        db_path=calendar_db, fernet=Fernet(Fernet.generate_key())
    )
    monkeypatch.setattr(api_main.settings, "internal_service_token", _INTERNAL_TOKEN)
    monkeypatch.setattr(api_main, "calendar_settings_repository", settings_repo)
    monkeypatch.setattr(api_main, "calendar_token_repository", token_repo)
    client = TestClient(api_app)
    yield {
        "client": client,
        "settings_repo": settings_repo,
        "token_repo": token_repo,
    }


# --- enable ----------------------------------------------------------------


def test_enable_requires_internal_token(env):
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 401


def test_operator_enable_becomes_designated_operator(env):
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "project_timezone": "Europe/Moscow",
            "lookahead_days": 30,
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "calendar_operator": _OPERATOR}
    stored = env["settings_repo"].get(_PROJECT_ID)
    assert stored.enabled is True
    assert stored.calendar_operator == _OPERATOR
    assert stored.lookahead_days == 30


def test_admin_enable_preserves_existing_operator(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": "@admin", "actor_role": "admin"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["calendar_operator"] == _OPERATOR


def test_admin_enable_on_fresh_project_has_no_operator(env):
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": "@admin", "actor_role": "admin"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["calendar_operator"] is None


def test_non_designated_operator_enable_rejected(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": _OTHER_OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_calendar_operator"


def test_unknown_actor_role_rejected(env):
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": _OPERATOR, "actor_role": "stranger"},
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "unknown_actor_role"


# --- disable ---------------------------------------------------------------


def test_operator_disable_keeps_token(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    env["token_repo"].upsert(_PROJECT_ID, _OPERATOR, "refresh-secret")
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/disable",
        json={"actor": _OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    assert env["settings_repo"].is_enabled(_PROJECT_ID) is False
    # Token retained — disable != delete.
    assert env["token_repo"].get_refresh_token(_PROJECT_ID, _OPERATOR) == "refresh-secret"


def test_admin_disable_keeps_token(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    env["token_repo"].upsert(_PROJECT_ID, _OPERATOR, "refresh-secret")
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/disable",
        json={"actor": "@admin", "actor_role": "admin"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert env["token_repo"].get_refresh_token(_PROJECT_ID, _OPERATOR) == "refresh-secret"


def test_non_designated_operator_disable_rejected(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/disable",
        json={"actor": _OTHER_OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert resp.status_code == 403


# --- admin-cannot-disconnect (FR-18/FR-21) ---------------------------------


def test_admin_disconnect_rejected_403(env, monkeypatch):
    """An admin attempting to disconnect/delete the integration → 403, and the
    token is left untouched."""
    monkeypatch.setattr(api_main, "calendar_oauth_client", AsyncMock())
    env["token_repo"].upsert(_PROJECT_ID, _OPERATOR, "refresh-secret")
    resp = env["client"].post(
        "/calendar/disconnect",
        json={
            "project_id": _PROJECT_ID,
            "operator": _OPERATOR,
            "actor_role": "admin",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_cannot_disconnect"
    # Operator-only delete: token remains.
    assert env["token_repo"].get_refresh_token(_PROJECT_ID, _OPERATOR) == "refresh-secret"


def test_operator_disconnect_allowed_deletes_token(env, monkeypatch):
    revoke = AsyncMock()
    oauth = AsyncMock()
    oauth.revoke = revoke
    monkeypatch.setattr(api_main, "calendar_oauth_client", oauth)
    env["token_repo"].upsert(_PROJECT_ID, _OPERATOR, "refresh-secret")
    resp = env["client"].post(
        "/calendar/disconnect",
        json={
            "project_id": _PROJECT_ID,
            "operator": _OPERATOR,
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": True}
    revoke.assert_awaited_once_with(refresh_token="refresh-secret")
    with pytest.raises(TokenNotFound):
        env["token_repo"].get_refresh_token(_PROJECT_ID, _OPERATOR)


# --- service rules ---------------------------------------------------------


def test_service_upsert_list_and_delete(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    create = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "name": "маникюр",
            "duration_minutes": 60,
            "service_days": ["mon", "tue"],
            "working_hours": {"mon": ["10:00", "19:00"]},
            "date_exceptions": ["2026-01-01"],
        },
        headers=_AUTH,
    )
    assert create.status_code == 200
    rule_id = create.json()["id"]

    view = env["client"].get(
        f"/calendar/projects/{_PROJECT_ID}/settings", headers=_AUTH
    )
    assert view.status_code == 200
    body = view.json()
    assert body["enabled"] is True
    assert body["calendar_operator"] == _OPERATOR
    assert body["project_timezone"] == "Europe/Moscow"
    assert len(body["service_rules"]) == 1
    rule = body["service_rules"][0]
    assert rule["name"] == "маникюр"
    assert rule["duration_minutes"] == 60
    assert rule["working_hours"] == {"mon": ["10:00", "19:00"]}

    # Update the same rule by id.
    update = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "rule_id": rule_id,
            "name": "стрижка",
            "duration_minutes": 30,
        },
        headers=_AUTH,
    )
    assert update.status_code == 200
    assert update.json()["id"] == rule_id

    delete = env["client"].request(
        "DELETE",
        f"/calendar/projects/{_PROJECT_ID}/services/{rule_id}",
        json={"actor": _OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert delete.status_code == 200
    assert delete.json() == {"deleted": True}
    assert env["settings_repo"].list_service_rules(_PROJECT_ID) == []


def test_admin_can_upsert_service(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        json={"actor": "@admin", "actor_role": "admin", "name": "x"},
        headers=_AUTH,
    )
    assert resp.status_code == 200


def test_service_delete_rejected_for_wrong_operator(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    rule_id = env["settings_repo"].upsert_service_rule(
        project_id=_PROJECT_ID, name="x"
    )
    resp = env["client"].request(
        "DELETE",
        f"/calendar/projects/{_PROJECT_ID}/services/{rule_id}",
        json={"actor": _OTHER_OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert resp.status_code == 403


def test_malformed_service_rule_rejected(env):
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "duration_minutes": -5,
        },
        headers=_AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_duration"
    assert env["settings_repo"].list_service_rules(_PROJECT_ID) == []


# --- settings view (no row) ------------------------------------------------


def test_settings_view_defaults_when_no_row(env):
    resp = env["client"].get(
        f"/calendar/projects/{_PROJECT_ID}/settings", headers=_AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "project_id": _PROJECT_ID,
        "enabled": False,
        "calendar_operator": None,
        "project_timezone": "Europe/Moscow",
        "lookahead_days": 60,
        "updated_at": None,
        "service_rules": [],
    }


def test_settings_view_requires_internal_token(env):
    resp = env["client"].get(f"/calendar/projects/{_PROJECT_ID}/settings")
    assert resp.status_code == 401
