from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.answerers import AnswerResult
from services.api.app.main import (
    answer_pipeline,
    answer_trace_repository,
    hitl_ticket_repository,
    incident_repository,
    rag_repository,
    settings,
    telegram_bot_sender,
)
from services.api.app.main import app as api_app


def _wire(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")


def _force_escalation(monkeypatch):
    monkeypatch.setattr(
        answer_pipeline, "run", AsyncMock(return_value=AnswerResult(handled=False))
    )


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-02")
def test_inbound_escalation_creates_and_assigns_hitl_ticket(tmp_path, monkeypatch):
    _wire(tmp_path)
    settings.hitl_primary_operator_username = "@ajdevy"
    _force_escalation(monkeypatch)
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound",
        json={"text": "Need escalation for this customer"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["escalated"] is True
    assert payload["hitl_operator_username"] == "@ajdevy"
    assert isinstance(payload["hitl_ticket_id"], int)

    tickets = client.get("/hitl/tickets").json()["items"]
    assert len(tickets) == 1
    assert tickets[0]["status"] == "assigned"
    assert tickets[0]["operator_username"] == "@ajdevy"
    assert tickets[0]["target_chat_id"] is None


def test_hitl_route_missing_operator_emits_incident(tmp_path):
    _wire(tmp_path)
    settings.hitl_primary_operator_username = ""
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-2", reason="uncertain")

    response = client.post(f"/hitl/tickets/{created.id}/route", json={"operator_username": None})
    assert response.status_code == 503
    assert response.json()["detail"] == "hitl_operator_missing"

    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


def test_hitl_route_and_resolve_endpoints(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-3", reason="policy")

    routed = client.post(f"/hitl/tickets/{created.id}/route", json={"operator_username": "@ops"})
    resolved = client.post(f"/hitl/tickets/{created.id}/resolve")
    assert routed.status_code == 200
    assert routed.json()["status"] == "assigned"
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-02-reply")
def test_hitl_reply_delivered_as_bot_authored_and_auto_resolves(tmp_path, monkeypatch):
    _wire(tmp_path)
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
    body = response.json()
    assert body["delivered"] is True
    assert body["resolved"] is True
    assert body["status"] == "resolved"
    mock_send.assert_awaited_once_with(chat_id=99887766, text="Here is the final answer.")

    # Confirm persistent state
    refreshed = hitl_ticket_repository.get(created.id)
    assert refreshed.status == "resolved"
    assert refreshed.resolved_at is not None


def test_hitl_reply_missing_target_chat_id_emits_incident(tmp_path):
    _wire(tmp_path)
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
    _wire(tmp_path)
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
    _wire(tmp_path)
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
    _wire(tmp_path)
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
        AsyncMock(side_effect=RuntimeError("missing_bot_token")),
    )

    response = client.post(
        f"/hitl/tickets/{created.id}/reply",
        json={"operator_username": "@ops", "reply_text": "answer"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "missing_bot_token"
    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


def test_inbound_uses_runtime_configured_hitl_operator(tmp_path, monkeypatch):
    _wire(tmp_path)
    settings.hitl_primary_operator_username = "@default"
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@flexsentlabs",
        updated_by="@ajdevy",
    )
    _force_escalation(monkeypatch)
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound", json={"text": "Need escalation"}
    )
    assert response.status_code == 200
    assert response.json()["hitl_operator_username"] == "@flexsentlabs"
