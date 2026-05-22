"""Epic 11 / story 11.08 — enable + configure E2E.

Admin enables a project → operator defines a service via the api → ``settings``
reflects both. Then verifies the permission split (FR-18/FR-21): an admin
attempting to disconnect is rejected (403) and the token survives, while the
operator can disconnect and delete it.
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

pytestmark = [pytest.mark.e2e, pytest.mark.epic("11"), pytest.mark.story("11-08")]

_INTERNAL_TOKEN = "e2e-internal-token"
_AUTH = {"Authorization": f"Bearer {_INTERNAL_TOKEN}"}
_PROJECT_ID = 11
_OPERATOR = "@calendar_op"
_ADMIN = "@admin"
_REFRESH_TOKEN = "e2e-refresh-secret"


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
    monkeypatch.setattr(api_main, "calendar_oauth_client", AsyncMock())
    client = TestClient(api_app)
    yield {"client": client, "token_repo": token_repo}


def test_epic11_enable_configure_and_permission_split(env):
    client = env["client"]
    token_repo = env["token_repo"]

    # 1) Operator connects first (so a token exists), establishing themselves as
    #    the designated calendar operator.
    enable = client.post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": _OPERATOR, "actor_role": "operator"},
        headers=_AUTH,
    )
    assert enable.status_code == 200
    assert enable.json()["calendar_operator"] == _OPERATOR
    token_repo.upsert(_PROJECT_ID, _OPERATOR, _REFRESH_TOKEN)

    # 2) Admin toggles the project off then back on — token must survive.
    off = client.post(
        f"/calendar/projects/{_PROJECT_ID}/disable",
        json={"actor": _ADMIN, "actor_role": "admin"},
        headers=_AUTH,
    )
    assert off.status_code == 200
    assert token_repo.get_refresh_token(_PROJECT_ID, _OPERATOR) == _REFRESH_TOKEN
    on_again = client.post(
        f"/calendar/projects/{_PROJECT_ID}/enable",
        json={"actor": _ADMIN, "actor_role": "admin"},
        headers=_AUTH,
    )
    assert on_again.status_code == 200
    # Admin re-enable preserves the designated operator.
    assert on_again.json()["calendar_operator"] == _OPERATOR

    # 3) Operator defines a service.
    service = client.post(
        f"/calendar/projects/{_PROJECT_ID}/services",
        json={
            "actor": _OPERATOR,
            "actor_role": "operator",
            "name": "маникюр",
            "duration_minutes": 60,
            "service_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "working_hours": {"mon": ["10:00", "19:00"]},
        },
        headers=_AUTH,
    )
    assert service.status_code == 200

    # 4) settings reflects enablement + the new service.
    view = client.get(
        f"/calendar/projects/{_PROJECT_ID}/settings", headers=_AUTH
    ).json()
    assert view["enabled"] is True
    assert view["calendar_operator"] == _OPERATOR
    assert len(view["service_rules"]) == 1
    assert view["service_rules"][0]["name"] == "маникюр"

    # 5) Admin attempts disconnect → 403 (operator-only), token untouched.
    admin_disconnect = client.post(
        "/calendar/disconnect",
        json={
            "project_id": _PROJECT_ID,
            "operator": _OPERATOR,
            "actor_role": "admin",
        },
        headers=_AUTH,
    )
    assert admin_disconnect.status_code == 403
    assert admin_disconnect.json()["detail"] == "admin_cannot_disconnect"
    assert token_repo.get_refresh_token(_PROJECT_ID, _OPERATOR) == _REFRESH_TOKEN

    # 6) Operator disconnect → token deleted.
    op_disconnect = client.post(
        "/calendar/disconnect",
        json={
            "project_id": _PROJECT_ID,
            "operator": _OPERATOR,
            "actor_role": "operator",
        },
        headers=_AUTH,
    )
    assert op_disconnect.status_code == 200
    with pytest.raises(TokenNotFound):
        token_repo.get_refresh_token(_PROJECT_ID, _OPERATOR)
