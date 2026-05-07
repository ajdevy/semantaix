"""Epic 03 + 04: guardrails block suggest → HITL ticket → route → resolve."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    settings,
)

pytestmark = [pytest.mark.e2e, pytest.mark.epic("04"), pytest.mark.story("04-01")]


def test_epic04_guardrail_blocked_suggest_then_route_and_resolve(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_username = "@ajdevy"
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    client = TestClient(api_app)

    suggest = client.post("/suggest", json={"text": "Customer needs an uncertain answer path."})
    assert suggest.status_code == 200
    blocked = suggest.json()
    assert blocked["response_mode"] == "blocked_invalid"
    ticket_id = blocked["hitl_ticket_id"]

    routed = client.post(
        f"/hitl/tickets/{ticket_id}/route",
        json={"operator_username": "@night_ops"},
    )
    assert routed.status_code == 200
    assert routed.json()["operator_username"] == "@night_ops"

    resolved = client.post(f"/hitl/tickets/{ticket_id}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
