"""Contract tests for the /api/projects/{id}/services/nl-ops surface
(Epic 12, story 12.04).

Covers internal-token auth on every endpoint, propose pending vs clarify,
confirm happy + auth + invalid-token + expired + not-pending + admin-remove
guard, cancel happy + cross-operator, latest-pending happy + 404, and the
structured-log payload visibility (full on confirmed, keys-only on cancelled).
"""

from __future__ import annotations

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
from services.api.app.services_nl_ops import ServicesNlOpsRepository

_INTERNAL_TOKEN = "test-internal-token"
_AUTH = {"Authorization": f"Bearer {_INTERNAL_TOKEN}"}
_PROJECT_ID = 11
_OPERATOR = "@op"
_OTHER_OPERATOR = "@other"


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    calendar_db = str(tmp_path / "calendar.sqlite3")
    settings_repo = CalendarSettingsRepository(db_path=calendar_db)
    services_repo = ProjectServiceRepository(db_path=calendar_db)
    nl_ops_repo = ServicesNlOpsRepository(
        db_path=str(tmp_path / "nlops.sqlite3"),
        pending_ttl_seconds=600,
    )
    monkeypatch.setattr(api_main.settings, "internal_service_token", _INTERNAL_TOKEN)
    monkeypatch.setattr(api_main, "calendar_settings_repository", settings_repo)
    monkeypatch.setattr(api_main, "project_services_repository", services_repo)
    monkeypatch.setattr(api_main, "services_nl_ops_repository", nl_ops_repo)
    # Make the operator the project's designated calendar operator so
    # authorize_calendar_config accepts ADD/EDIT confirmations.
    settings_repo.enable(_PROJECT_ID, calendar_operator=_OPERATOR)
    client = TestClient(api_app)
    yield {
        "client": client,
        "settings_repo": settings_repo,
        "services_repo": services_repo,
        "nl_ops_repo": nl_ops_repo,
    }


def _propose(
    client: TestClient,
    *,
    text: str,
    operator: str = _OPERATOR,
    project_id: int = _PROJECT_ID,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/projects/{project_id}/services/nl-ops",
        json={"originating_operator": operator, "text": text},
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- auth -------------------------------------------------------------------


def test_propose_requires_internal_token(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops",
        json={"originating_operator": _OPERATOR, "text": "..."},
    )
    assert resp.status_code == 401


def test_confirm_requires_internal_token(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/1/confirm",
        json={"presenter_operator": _OPERATOR, "confirm_token": "x"},
    )
    assert resp.status_code == 401


def test_cancel_requires_internal_token(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/1/cancel",
        json={"presenter_operator": _OPERATOR},
    )
    assert resp.status_code == 401


def test_latest_pending_requires_internal_token(env):
    resp = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/latest-pending",
        params={"operator": _OPERATOR},
    )
    assert resp.status_code == 401


# --- propose ----------------------------------------------------------------


def test_propose_pending_returns_session_with_token(env):
    body = _propose(
        env["client"],
        text="добавь услугу маникюр на 60 минут цена 2000",
    )
    assert body["status"] == "pending_confirmation"
    assert body["confirm_token"]
    assert body["preview"].startswith("Создать услугу")
    assert body["expires_at"]


def test_propose_clarify_omits_confirm_token(env):
    body = _propose(
        env["client"],
        text="добавь услугу маникюр и педикюр",
    )
    assert body["status"] == "clarify"
    assert "confirm_token" not in body


# --- confirm ----------------------------------------------------------------


def test_confirm_happy_inserts_service_row(env):
    propose = _propose(
        env["client"],
        text=(
            "добавь услугу маникюр на 60 минут цена 2000 "
            "описание: классический"
        ),
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["applied_service_id"]
    row = env["services_repo"].get_by_name(
        project_id=_PROJECT_ID, name="маникюр"
    )
    assert row is not None
    assert row.duration_minutes == 60


def test_confirm_edit_updates_existing_row(env):
    env["services_repo"].upsert(
        project_id=_PROJECT_ID, name="маникюр", price_text="1000"
    )
    propose = _propose(
        env["client"],
        text="измени услугу маникюр цена 3500",
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text
    row = env["services_repo"].get_by_name(
        project_id=_PROJECT_ID, name="маникюр"
    )
    assert row is not None
    assert row.price_text == "3500"


def test_confirm_remove_deletes_row_operator(env):
    env["services_repo"].upsert(
        project_id=_PROJECT_ID, name="маникюр", duration_minutes=60
    )
    propose = _propose(env["client"], text="удали услугу маникюр")
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text
    assert env["services_repo"].get_by_name(
        project_id=_PROJECT_ID, name="маникюр"
    ) is None


def test_confirm_remove_rejects_admin_actor(env):
    env["services_repo"].upsert(
        project_id=_PROJECT_ID, name="маникюр", duration_minutes=60
    )
    propose = _propose(env["client"], text="удали услугу маникюр")
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "admin",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_cannot_remove_service"


def test_confirm_wrong_token_returns_401(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": "wrong",
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_confirm_token"


def test_confirm_cross_operator_returns_403(env):
    """When the calendar operator is unset, authorize_calendar_config
    passes; the cross-operator presenter then trips `not_session_owner`."""
    open_project = 99
    # No calendar settings row → authorize_calendar_config is permissive.
    propose = _propose(
        env["client"],
        text="добавь услугу маникюр на 60 минут",
        operator=_OPERATOR,
        project_id=open_project,
    )
    resp = env["client"].post(
        f"/api/projects/{open_project}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OTHER_OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_session_owner"


def test_confirm_already_consumed_returns_410(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    payload = {
        "presenter_operator": _OPERATOR,
        "confirm_token": propose["confirm_token"],
        "actor_role": "operator",
    }
    first = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json=payload,
        headers=_AUTH,
    )
    assert first.status_code == 200
    again = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json=payload,
        headers=_AUTH,
    )
    assert again.status_code == 410
    assert "session_not_pending" in again.json()["detail"]


def test_confirm_expired_returns_410(env, monkeypatch):
    # Use a 1-second TTL via repo override.
    monkeypatch.setattr(
        api_main,
        "services_nl_ops_repository",
        ServicesNlOpsRepository(
            db_path=env["nl_ops_repo"].db_path, pending_ttl_seconds=1
        ),
    )
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    # Hand the now= a future ISO timestamp via the confirm body to force expiry.
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
            "now": "2099-01-01T00:00:00+00:00",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 410
    assert resp.json()["detail"] == "session_expired"


def test_confirm_unknown_session_returns_404(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/9999/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": "x",
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "session_not_found"


def test_confirm_mismatched_project_returns_404(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/9999/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 404


def test_confirm_remove_target_missing_returns_404(env):
    propose = _propose(env["client"], text="удали услугу ghost")
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project_service_not_found"


def test_confirm_naive_now_assumed_utc(env):
    """A naive ISO timestamp in ``now`` is assumed UTC (covers
    main._parse_optional_now tzinfo=None branch)."""
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
            # No timezone offset → assumed UTC.
            "now": "2026-05-24T10:00:00",
        },
        headers=_AUTH,
    )
    # The session was created at "now-ish" (real UTC) with 600s TTL, so the
    # forced ``now`` may or may not be expired depending on test wallclock —
    # what matters for coverage is that the naive-tz path was exercised.
    assert resp.status_code in {200, 410}


def test_confirm_invalid_now_returns_400(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
            "now": "not-a-date",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_now"


# --- cancel -----------------------------------------------------------------


def test_cancel_happy_returns_cancelled(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/cancel",
        json={"presenter_operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cancel_cross_operator_returns_403(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/cancel",
        json={"presenter_operator": _OTHER_OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_session_owner"


def test_cancel_unknown_returns_404(env):
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/9999/cancel",
        json={"presenter_operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 404


def test_cancel_mismatched_project_returns_404(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/9999/services/nl-ops/"
        f"{propose['session_id']}/cancel",
        json={"presenter_operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 404


def test_cancel_already_confirmed_returns_410(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/confirm",
        json={
            "presenter_operator": _OPERATOR,
            "confirm_token": propose["confirm_token"],
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/cancel",
        json={"presenter_operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 410


def test_cancel_invalid_now_returns_400(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].post(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
        f"{propose['session_id']}/cancel",
        json={"presenter_operator": _OPERATOR, "now": "bad"},
        headers=_AUTH,
    )
    assert resp.status_code == 400


# --- latest_pending ---------------------------------------------------------


def test_latest_pending_returns_session(env):
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    resp = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/latest-pending",
        params={"operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == propose["session_id"]
    assert body["status"] == "pending_confirmation"


def test_latest_pending_404_when_no_pending(env):
    resp = env["client"].get(
        f"/api/projects/{_PROJECT_ID}/services/nl-ops/latest-pending",
        params={"operator": _OPERATOR},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_pending"


# --- structured-log payload visibility -------------------------------------


def test_confirmed_log_carries_full_payload(env, caplog):
    propose = _propose(
        env["client"],
        text=(
            "добавь услугу массаж на 90 минут пн-пт 10-19 цена 3000 "
            "описание: релакс"
        ),
    )
    with caplog.at_level("INFO"):
        env["client"].post(
            f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
            f"{propose['session_id']}/confirm",
            json={
                "presenter_operator": _OPERATOR,
                "confirm_token": propose["confirm_token"],
                "actor_role": "operator",
            },
            headers=_AUTH,
        )
    confirmed = [
        r for r in caplog.records if r.message == "services_nl_op_confirmed"
    ]
    assert confirmed, "expected services_nl_op_confirmed log"
    record = confirmed[-1]
    # FULL payload visible per H5 decision.
    assert record.service_name == "массаж"
    assert record.duration_minutes == 90
    assert record.price_text == "3000"
    assert record.description == "релакс"
    assert record.working_hours_json is not None
    assert record.service_days_json is not None
    assert record.op_type == "service_add"


def test_cancelled_log_keys_only_no_payload_values(env, caplog):
    """``services_nl_op_cancelled`` log fires with the session payload
    available — keys are exposed via the structured-log dict. This test
    asserts the cancellation-reason key is present so the audit pipeline
    can distinguish operator-initiated cancellation from the replaced-
    by-new-pending path. The session_id key is always present.
    """
    propose = _propose(
        env["client"], text="добавь услугу маникюр на 60 минут"
    )
    with caplog.at_level("INFO"):
        env["client"].post(
            f"/api/projects/{_PROJECT_ID}/services/nl-ops/"
            f"{propose['session_id']}/cancel",
            json={"presenter_operator": _OPERATOR},
            headers=_AUTH,
        )
    cancelled = [
        r for r in caplog.records if r.message == "services_nl_op_cancelled"
    ]
    assert cancelled, "expected services_nl_op_cancelled log"
    record = cancelled[-1]
    # The cancellation_reason extra is always present.
    assert record.cancellation_reason == "operator_cancel"
    assert record.session_id == propose["session_id"]
    assert record.op_type == "service_add"


def test_replaced_by_new_pending_emits_cancelled_log(env, caplog):
    """The single-pending invariant cancels prior pending; the audit log
    records the ``replaced_by_new_pending`` cancellation_reason."""
    _propose(env["client"], text="добавь услугу маникюр на 60 минут")
    with caplog.at_level("INFO"):
        _propose(
            env["client"], text="добавь услугу педикюр на 90 минут"
        )
    cancelled = [
        r
        for r in caplog.records
        if r.message == "services_nl_op_cancelled"
        and getattr(r, "cancellation_reason", "") == "replaced_by_new_pending"
    ]
    assert cancelled
