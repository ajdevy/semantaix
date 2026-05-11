from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    settings,
    telegram_bot_sender,
)


@pytest.mark.e2e
@pytest.mark.epic("03")
@pytest.mark.epic("04")
@pytest.mark.story("04-02")
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


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-02-reply")
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


def test_effective_hitl_operator_chat_id_prefers_runtime_config(tmp_path):
    from services.api.app.main import _effective_hitl_operator_chat_id

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = "111"
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value="222",
        updated_by="@ajdevy",
    )
    assert _effective_hitl_operator_chat_id() == "222"


def test_effective_hitl_operator_chat_id_falls_back_to_env(tmp_path):
    from services.api.app.main import _effective_hitl_operator_chat_id

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = "333"
    assert _effective_hitl_operator_chat_id() == "333"


def test_effective_hitl_operator_chat_id_none_when_unset(tmp_path):
    from services.api.app.main import _effective_hitl_operator_chat_id

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = None
    assert _effective_hitl_operator_chat_id() is None


@pytest.mark.asyncio
async def test_notify_hitl_operator_skips_when_no_chat_id(tmp_path, monkeypatch):
    from services.api.app.main import _notify_hitl_operator

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = None
    mock_send = AsyncMock()
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    sent = await _notify_hitl_operator(ticket_id=1, summary="x")
    assert sent is False
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_hitl_operator_returns_false_for_non_numeric_chat_id(
    tmp_path, monkeypatch
):
    from services.api.app.main import _notify_hitl_operator

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = "not-a-number"
    mock_send = AsyncMock()
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    sent = await _notify_hitl_operator(ticket_id=1, summary="x")
    assert sent is False
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_hitl_operator_returns_false_on_missing_bot_token(
    tmp_path, monkeypatch
):
    from services.api.app.main import _notify_hitl_operator

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = "650934815"
    monkeypatch.setattr(
        telegram_bot_sender,
        "send_message",
        AsyncMock(side_effect=RuntimeError("missing_bot_token")),
    )

    sent = await _notify_hitl_operator(ticket_id=1, summary="x")
    assert sent is False


@pytest.mark.asyncio
async def test_notify_hitl_operator_sends_and_returns_true(tmp_path, monkeypatch):
    from services.api.app.main import _notify_hitl_operator

    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    settings.hitl_primary_operator_chat_id = "650934815"
    mock_send = AsyncMock(return_value=999)
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    sent = await _notify_hitl_operator(ticket_id=42, summary="created — low_confidence")
    assert sent is True
    mock_send.assert_awaited_once_with(
        chat_id=650934815,
        text="HITL ticket #42: created — low_confidence",
    )


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-operator-dm")
def test_suggest_blocked_dms_operator_when_chat_id_configured(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_username = "@flexsentlabs"
    settings.hitl_primary_operator_chat_id = "650934815"
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    mock_send = AsyncMock(return_value=1234)
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Need escalation"})

    assert response.status_code == 200
    assert response.json()["response_mode"] == "blocked_invalid"
    mock_send.assert_awaited_once()
    call_kwargs = mock_send.await_args.kwargs
    assert call_kwargs["chat_id"] == 650934815
    assert call_kwargs["text"].startswith("HITL ticket #")
    assert "low_confidence" in call_kwargs["text"]


def test_suggest_blocked_skips_dm_when_chat_id_missing(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_chat_id = None
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    mock_send = AsyncMock()
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)

    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Need escalation"})

    assert response.status_code == 200
    mock_send.assert_not_called()


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-operator-dm")
def test_hitl_route_dms_operator_when_chat_id_configured(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings.hitl_primary_operator_chat_id = "650934815"
    mock_send = AsyncMock(return_value=4321)
    monkeypatch.setattr(telegram_bot_sender, "send_message", mock_send)
    client = TestClient(api_app)
    created = hitl_ticket_repository.create(conversation_ref="conv-route-dm", reason="policy")

    response = client.post(
        f"/hitl/tickets/{created.id}/route",
        json={"operator_username": "@flexsentlabs"},
    )

    assert response.status_code == 200
    assert response.json()["operator_username"] == "@flexsentlabs"
    mock_send.assert_awaited_once_with(
        chat_id=650934815,
        text=f"HITL ticket #{created.id}: assigned to @flexsentlabs",
    )
