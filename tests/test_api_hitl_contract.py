from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    settings,
)


def test_invalid_suggest_creates_and_assigns_hitl_ticket(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_username = "@ajdevy"
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    client = TestClient(api_app)

    response = client.post("/suggest", json={"text": "Need escalation for this customer"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["response_mode"] == "blocked_invalid"
    assert payload["hitl_operator_username"] == "@ajdevy"
    assert isinstance(payload["hitl_ticket_id"], int)

    tickets = client.get("/hitl/tickets").json()["items"]
    assert len(tickets) == 1
    assert tickets[0]["status"] == "assigned"
    assert tickets[0]["operator_username"] == "@ajdevy"


def test_hitl_route_missing_operator_emits_incident(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_username = ""
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-2", reason="uncertain")

    response = client.post(f"/hitl/tickets/{created.id}/route", json={"operator_username": None})
    assert response.status_code == 503
    assert response.json()["detail"] == "hitl_operator_missing"

    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


def test_hitl_route_and_resolve_endpoints(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-3", reason="policy")

    routed = client.post(f"/hitl/tickets/{created.id}/route", json={"operator_username": "@ops"})
    resolved = client.post(f"/hitl/tickets/{created.id}/resolve")
    assert routed.status_code == 200
    assert routed.json()["status"] == "assigned"
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
