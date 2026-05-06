from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    settings,
    telegram_bot_sender,
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
    assert tickets[0]["target_chat_id"] is None


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


def test_hitl_reply_delivered_as_bot_authored(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(
        conversation_ref="conv-4",
        reason="low_confidence",
        target_chat_id=99887766,
    )
    hitl_ticket_repository.assign(ticket_id=created.id, operator_username="@ops")
    mock_send = AsyncMock(return_value=4242)
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@ops", "reply_text": "Here is the final answer."},
    )
    assert response.status_code == 200
    assert response.json()["delivered"] is True
    mock_send.assert_awaited_once_with(chat_id=99887766, text="Here is the final answer.")


def test_hitl_reply_missing_target_chat_id_emits_incident(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-5", reason="policy")
    hitl_ticket_repository.assign(ticket_id=created.id, operator_username="@ops")

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@ops", "reply_text": "Answer body"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "missing_target_chat_id"
    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


def test_hitl_reply_rejects_non_assigned_operator(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(
        conversation_ref="conv-6",
        reason="policy",
        target_chat_id=111,
    )
    hitl_ticket_repository.assign(ticket_id=created.id, operator_username="@ops")

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@other", "reply_text": "Nope"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "operator_not_assigned"


def test_hitl_reply_rejects_empty_reply(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(
        conversation_ref="conv-7",
        reason="policy",
        target_chat_id=222,
    )
    hitl_ticket_repository.assign(ticket_id=created.id, operator_username="@ops")

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@ops", "reply_text": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "empty_reply"


def test_hitl_reply_missing_bot_token_emits_incident(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(
        conversation_ref="conv-8",
        reason="policy",
        target_chat_id=333,
    )
    hitl_ticket_repository.assign(ticket_id=created.id, operator_username="@ops")
    monkeypatch.setattr(
        telegram_bot_sender,
        "send_message",
        AsyncMock(side_effect=RuntimeError("x")),
    )

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@ops", "reply_text": "answer"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "x"
    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


def test_suggest_uses_runtime_configured_hitl_operator(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_username = "@default"
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@flexsentlabs",
        updated_by="@ajdevy",
    )
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    client = TestClient(api_app)

    response = client.post("/suggest", json={"text": "Need escalation"})
    assert response.status_code == 200
    assert response.json()["hitl_operator_username"] == "@flexsentlabs"
