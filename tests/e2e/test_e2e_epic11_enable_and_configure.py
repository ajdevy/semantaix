"""Epic 11 / story 11.08 — connect-as-enable + configure E2E.

`/connect_calendar` IS the enable action (PR #75 follow-up): a successful
OAuth callback flips the project to enabled and records the connecting
operator as the designated calendar operator atomically with the token
upsert. There is no standalone `/calendar_on` command or `/enable` endpoint.

This test drives the OAuth callback (Google mocked) to enable, then the
operator defines a service via the api → ``settings`` reflects both. It also
verifies the permission split (FR-18/FR-21): admin can `/calendar_off` and
the token survives; re-enable means re-running the OAuth flow; admin cannot
disconnect/delete (operator-only).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.calendar.oauth import CalendarOAuthClient
from services.api.app.calendar.oauth_state_repository import (
    CalendarOAuthStateRepository,
)
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


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    api_main._calendar_oauth_hits.clear()
    yield
    api_main._calendar_oauth_hits.clear()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    calendar_db = str(tmp_path / "calendar.sqlite3")
    settings_repo = CalendarSettingsRepository(db_path=calendar_db)
    state_repo = CalendarOAuthStateRepository(db_path=calendar_db)
    token_repo = CalendarTokenRepository(
        db_path=calendar_db, fernet=Fernet(Fernet.generate_key())
    )
    oauth_client = CalendarOAuthClient(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://example.test/calendar/oauth/callback",
    )
    monkeypatch.setattr(api_main.settings, "internal_service_token", _INTERNAL_TOKEN)
    monkeypatch.setattr(api_main.settings, "calendar_oauth_state_ttl_seconds", 300)
    monkeypatch.setattr(api_main, "calendar_settings_repository", settings_repo)
    monkeypatch.setattr(api_main, "calendar_oauth_state_repository", state_repo)
    monkeypatch.setattr(api_main, "calendar_token_repository", token_repo)
    monkeypatch.setattr(api_main, "calendar_oauth_client", oauth_client)
    client = TestClient(api_app)
    yield {
        "client": client,
        "settings_repo": settings_repo,
        "state_repo": state_repo,
        "token_repo": token_repo,
    }


def _stub_google_exchange(monkeypatch, *, refresh_token: str) -> None:
    flow = Mock()
    flow.fetch_token = Mock()
    flow.credentials = SimpleNamespace(
        refresh_token=refresh_token, token="access", expiry=None
    )
    monkeypatch.setattr(
        "services.api.app.calendar.oauth.Flow.from_client_config",
        lambda config, scopes: flow,
    )


def _connect_via_oauth_callback(env, monkeypatch, *, refresh_token: str) -> None:
    """Drive a full /connect_calendar round-trip with Google mocked."""
    _stub_google_exchange(monkeypatch, refresh_token=refresh_token)
    state = env["state_repo"].create(
        project_id=_PROJECT_ID,
        operator=_OPERATOR,
        ttl_seconds=300,
        now=datetime.now(UTC),
    )
    resp = env["client"].get(
        "/calendar/oauth/callback", params={"state": state, "code": "auth-code"}
    )
    assert resp.status_code == 200


def test_epic11_enable_configure_and_permission_split(env, monkeypatch):
    client = env["client"]
    settings_repo = env["settings_repo"]
    token_repo = env["token_repo"]

    # 1) Operator connects → connect IS enable. Project becomes enabled and
    #    the operator is the designated calendar operator atomically with
    #    the token upsert.
    _connect_via_oauth_callback(env, monkeypatch, refresh_token=_REFRESH_TOKEN)
    stored = settings_repo.get(_PROJECT_ID)
    assert stored.enabled is True
    assert stored.calendar_operator == _OPERATOR
    assert token_repo.get_refresh_token(_PROJECT_ID, _OPERATOR) == _REFRESH_TOKEN

    # 2) Admin disables — token must survive; re-enable requires the operator
    #    to re-run /connect_calendar (no admin enable path exists).
    off = client.post(
        f"/calendar/projects/{_PROJECT_ID}/disable",
        json={"actor": _ADMIN, "actor_role": "admin"},
        headers=_AUTH,
    )
    assert off.status_code == 200
    assert settings_repo.is_enabled(_PROJECT_ID) is False
    assert token_repo.get_refresh_token(_PROJECT_ID, _OPERATOR) == _REFRESH_TOKEN

    # 3) Re-enable = re-run /connect_calendar. Existing project_timezone /
    #    lookahead_days are preserved.
    _connect_via_oauth_callback(env, monkeypatch, refresh_token=_REFRESH_TOKEN)
    stored = settings_repo.get(_PROJECT_ID)
    assert stored.enabled is True
    assert stored.calendar_operator == _OPERATOR

    # 4) Operator defines a service.
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

    # 5) settings reflects enablement + the new service.
    view = client.get(
        f"/calendar/projects/{_PROJECT_ID}/settings", headers=_AUTH
    ).json()
    assert view["enabled"] is True
    assert view["calendar_operator"] == _OPERATOR
    assert len(view["service_rules"]) == 1
    assert view["service_rules"][0]["name"] == "маникюр"

    # 6) Admin attempts disconnect → 403 (operator-only), token untouched.
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

    # 7) Operator disconnect → token deleted. Use a no-op revoke since the real
    #    OAuth client would hit Google.
    monkeypatch.setattr(api_main.calendar_oauth_client, "revoke", AsyncMock())
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
