"""Contract tests for the canonical ``/api/projects/{id}/services`` surface
(Epic 12, story 12.02).

Covers:
- ``GET`` empty vs populated.
- ``POST`` operator + admin (add/edit shared per FR-21); case-insensitive
  upsert collapses ``маникюр`` / ``МАНИКЮР`` to a single row.
- ``POST`` validation: every reason code returns 400.
- ``DELETE`` operator → 200; admin → 403 ``admin_cannot_remove_service``
  (FR-18/FR-21 destructive-op rule); unknown id → 404
  ``project_service_not_found``; unknown role → 400 ``unknown_actor_role``.
- Internal-token Bearer auth enforced on every method.
- Single-flight: per-``(project, lower(name))`` lock serializes concurrent
  upserts (no DB-level race observed even with interleaved repo calls).
"""

from __future__ import annotations

import asyncio
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
_PROJECT_ID = 41
_OPERATOR = "@op"
_ADMIN = "@admin"


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


# --- auth ------------------------------------------------------------------


def test_list_requires_internal_token(env):
    resp = env["client"].get(f"/api/projects/{_PROJECT_ID}/services")
    assert resp.status_code == 401


def test_upsert_requires_internal_token(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        json={"actor": _OPERATOR, "actor_role": "operator", "name": "x"},
    )
    assert resp.status_code == 401


def test_delete_requires_internal_token(env):
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/1",
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 401


# --- GET -------------------------------------------------------------------


def test_get_empty_returns_no_services(env):
    resp = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    )
    assert resp.status_code == 200
    assert resp.json() == {"project_id": _PROJECT_ID, "services": []}


def test_get_returns_full_service_shape(env):
    env["services_repo"].upsert(
        project_id=_PROJECT_ID,
        name="маникюр",
        description="классический",
        price_text="от 2000 ₽",
        tags=["nails"],
        duration_minutes=60,
        working_hours={"sat": [["10:00", "19:00"]]},
        service_days=["sat"],
        date_exceptions=["2026-01-07"],
    )
    resp = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == _PROJECT_ID
    assert len(body["services"]) == 1
    row = body["services"][0]
    assert row["name"] == "маникюр"
    assert row["description"] == "классический"
    assert row["price_text"] == "от 2000 ₽"
    assert row["tags"] == ["nails"]
    assert row["duration_minutes"] == 60
    assert row["working_hours"] == {"sat": [["10:00", "19:00"]]}
    assert row["service_days"] == ["sat"]
    assert row["date_exceptions"] == ["2026-01-07"]
    assert row["updated_at"] is not None


# --- POST ------------------------------------------------------------------


def test_operator_upsert_returns_full_row(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "name": "маникюр",
            "duration_minutes": 60,
            "working_hours": {"mon": [["10:00", "19:00"]]},
            "service_days": ["mon"],
            "date_exceptions": ["2026-01-01"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == _PROJECT_ID
    assert body["name"] == "маникюр"
    assert body["duration_minutes"] == 60
    # Visible via GET.
    listing = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    ).json()
    assert [s["name"] for s in listing["services"]] == ["маникюр"]


def test_admin_upsert_allowed(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": _ADMIN, "actor_role": "admin", "name": "x"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "x"


def test_upsert_name_only_succeeds(env):
    """R1: name-only upsert creates a catalog-only entry (no scheduling)."""
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator", "name": "маникюр"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "маникюр"
    assert body["duration_minutes"] is None
    assert body["working_hours"] is None
    assert body["service_days"] is None
    assert body["date_exceptions"] is None
    # GET returns the same shape.
    listing = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    ).json()
    assert len(listing["services"]) == 1
    row = listing["services"][0]
    assert row["name"] == "маникюр"
    assert row["duration_minutes"] is None
    assert row["working_hours"] is None
    assert row["service_days"] is None
    assert row["date_exceptions"] is None


def test_upsert_case_insensitive_collapses_to_one_row(env):
    first = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator", "name": "маникюр"},
    )
    assert first.status_code == 200
    first_id = first.json()["id"]
    second = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "name": "МАНИКЮР",
            "description": "обновлённое",
        },
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.json()["description"] == "обновлённое"
    listing = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services", headers=_AUTH
    ).json()
    assert len(listing["services"]) == 1


def test_upsert_rejects_designated_operator_mismatch(env):
    # FR-21: operator must be the project's designated calendar operator
    # (when one is set). This is delegated to ``authorize_calendar_config``.
    env["settings_repo"].enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": "@other", "actor_role": "operator", "name": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_calendar_operator"


def test_upsert_rejects_unknown_actor_role(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "ghost", "name": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "unknown_actor_role"


# --- POST validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("payload_override", "expected_reason"),
    [
        ({"name": "   "}, "invalid_service_name"),
        ({"name": "x", "duration_minutes": -5}, "invalid_duration"),
        (
            {"name": "x", "working_hours": {"mon": [["19:00", "10:00"]]}},
            "invalid_working_hours",
        ),
        ({"name": "x", "service_days": ["funday"]}, "invalid_service_days"),
        (
            {"name": "x", "date_exceptions": ["not-an-iso-date"]},
            "invalid_date_exceptions",
        ),
    ],
)
def test_upsert_validation_returns_400(env, payload_override, expected_reason):
    body = {"actor": _OPERATOR, "actor_role": "operator"}
    body.update(payload_override)
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services",
        headers=_AUTH,
        json=body,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == expected_reason
    # Nothing got persisted.
    assert env["services_repo"].list_for_project(project_id=_PROJECT_ID) == []


# --- DELETE ----------------------------------------------------------------


def test_delete_operator_succeeds(env):
    created = env["services_repo"].upsert(project_id=_PROJECT_ID, name="x")
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/{created.id}",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert env["services_repo"].list_for_project(project_id=_PROJECT_ID) == []


def test_delete_admin_rejected_403(env):
    created = env["services_repo"].upsert(project_id=_PROJECT_ID, name="x")
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/{created.id}",
        headers=_AUTH,
        json={"actor": _ADMIN, "actor_role": "admin"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_cannot_remove_service"
    # Destructive op blocked: row still there.
    rows = env["services_repo"].list_for_project(project_id=_PROJECT_ID)
    assert len(rows) == 1


def test_delete_unknown_role_rejected_400(env):
    created = env["services_repo"].upsert(project_id=_PROJECT_ID, name="x")
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/{created.id}",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "ghost"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_actor_role"


def test_delete_missing_returns_404(env):
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/9999",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project_service_not_found"


def test_delete_race_under_lock_also_returns_404(env, monkeypatch):
    """Lookup succeeds, but the row is gone by the time the lock-guarded
    delete fires (alias path raced in). The inner ``ProjectServiceNotFound``
    must still translate to 404, not bubble as a 500.
    """
    from services.api.app.calendar.project_services_repository import (
        ProjectServiceNotFound,
    )

    created = env["services_repo"].upsert(project_id=_PROJECT_ID, name="x")

    def raising_delete(**_kwargs):
        raise ProjectServiceNotFound("simulated_race")

    monkeypatch.setattr(env["services_repo"], "delete", raising_delete)
    resp = env["client"].request(
        "DELETE",
        f"/api/projects/{_PROJECT_ID}/services/{created.id}",
        headers=_AUTH,
        json={"actor": _OPERATOR, "actor_role": "operator"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project_service_not_found"


# --- Single-flight lock ----------------------------------------------------


def test_upsert_lock_serializes_concurrent_same_name(env, monkeypatch):
    """Two concurrent upserts for the same ``(project_id, lower(name))`` may
    not overlap inside the repo — the per-row lock ensures the second waits.

    Invokes the endpoint handler directly (rather than via TestClient) so both
    coroutines share a single asyncio loop where the module-level
    ``acquire_service_upsert_lock`` cache can return the same Lock to both.
    """
    overlap_detected = False
    in_flight = 0

    real_upsert = env["services_repo"].upsert

    def tracking_upsert(**kwargs):
        nonlocal overlap_detected, in_flight
        in_flight += 1
        try:
            if in_flight > 1:
                overlap_detected = True
            # Yield to the other thread so any racing call has a chance to
            # interleave (without the lock, two callers WOULD overlap here).
            import time
            time.sleep(0.05)
            return real_upsert(**kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(env["services_repo"], "upsert", tracking_upsert)
    # Reset the module-level lock cache so this test gets a fresh asyncio.Lock
    # bound to the loop ``asyncio.run`` creates below.
    from services.api.app.calendar import project_services_repository as psr

    psr._LOCKS.clear()
    psr._LOCKS_GUARD = None

    request = api_main.ProjectServiceUpsertRequest(
        actor=_OPERATOR, actor_role="operator", name="одинаковый"
    )

    async def driver():
        return await asyncio.gather(
            api_main.api_project_services_upsert(
                _PROJECT_ID, request, _principal="internal"
            ),
            api_main.api_project_services_upsert(
                _PROJECT_ID, request, _principal="internal"
            ),
        )

    results = asyncio.run(driver())
    assert all(r["name"] == "одинаковый" for r in results)
    assert overlap_detected is False
    rows = env["services_repo"].list_for_project(project_id=_PROJECT_ID)
    assert len(rows) == 1
    # Reset so leaks don't bleed into other tests.
    psr._LOCKS.clear()
    psr._LOCKS_GUARD = None
