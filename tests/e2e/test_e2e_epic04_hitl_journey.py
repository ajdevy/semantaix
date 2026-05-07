"""Epic 03 + 04: guardrails block suggest -> HITL ticket -> route -> reply -> resolve."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    rag_repository,
    settings,
    telegram_bot_sender,
)
from services.bot_gateway.app.main import (
    app as bot_app,
)
from services.bot_gateway.app.main import (
    hitl_ticket_repository as bot_hitl_repo,
)

pytestmark = [pytest.mark.e2e, pytest.mark.epic("04")]


def _wire(tmp_path):
    hitl_path = str(tmp_path / "hitl.sqlite3")
    hitl_ticket_repository.db_path = hitl_path
    bot_hitl_repo.db_path = hitl_path
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    settings.hitl_primary_operator_username = "@ajdevy"


@pytest.mark.story("04-01")
def test_epic04_guardrail_blocked_suggest_then_route_and_resolve(tmp_path, monkeypatch):
    _wire(tmp_path)
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


@pytest.mark.story("04-02")
def test_epic04_full_bot_authored_reply_chain(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    send_mock = AsyncMock(return_value=12345)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send_mock)
    client = TestClient(api_app)

    blocked = client.post(
        "/suggest",
        json={"text": "Help me with this question.", "chat_id": 9001},
    ).json()
    ticket_id = blocked["hitl_ticket_id"]

    tickets = client.get("/hitl/tickets").json()["items"]
    assert tickets[0]["target_chat_id"] == 9001

    client.post(
        f"/hitl/tickets/{ticket_id}/route",
        json={"operator_username": "@operator_a"},
    )
    reply = client.post(
        f"/hitl/tickets/{ticket_id}/reply",
        json={"operator_username": "@operator_a", "reply_text": "Here is the answer."},
    )
    assert reply.status_code == 200
    assert reply.json()["delivered"] is True
    send_mock.assert_awaited_once_with(chat_id=9001, text="Here is the answer.")

    resolved = client.post(f"/hitl/tickets/{ticket_id}/resolve")
    assert resolved.json()["status"] == "resolved"


@pytest.mark.story("04-01")
def test_epic04_route_without_operator_emits_incident(tmp_path, monkeypatch):
    _wire(tmp_path)
    settings.hitl_primary_operator_username = ""
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    client = TestClient(api_app)

    blocked = client.post("/suggest", json={"text": "Question."}).json()
    ticket_id = blocked["hitl_ticket_id"]

    response = client.post(f"/hitl/tickets/{ticket_id}/route", json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "hitl_operator_missing"

    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"


@pytest.mark.story("04-02")
def test_epic04_reply_missing_target_chat_id_emits_incident(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    send_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send_mock)
    client = TestClient(api_app)

    # No chat_id supplied -> ticket created with target_chat_id=None
    blocked = client.post("/suggest", json={"text": "Question without chat."}).json()
    ticket_id = blocked["hitl_ticket_id"]

    client.post(
        f"/hitl/tickets/{ticket_id}/route",
        json={"operator_username": "@operator_a"},
    )
    response = client.post(
        f"/hitl/tickets/{ticket_id}/reply",
        json={"operator_username": "@operator_a", "reply_text": "Reply"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "missing_target_chat_id"
    send_mock.assert_not_awaited()

    incidents = client.get("/incidents/hitl_delivery_failures").json()["items"]
    assert len(incidents) == 1


@pytest.mark.story("04-02")
def test_epic04_reply_rejects_non_assigned_operator(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    send_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send_mock)
    client = TestClient(api_app)

    blocked = client.post(
        "/suggest",
        json={"text": "Question.", "chat_id": 42},
    ).json()
    ticket_id = blocked["hitl_ticket_id"]

    client.post(
        f"/hitl/tickets/{ticket_id}/route",
        json={"operator_username": "@operator_a"},
    )
    response = client.post(
        f"/hitl/tickets/{ticket_id}/reply",
        json={"operator_username": "@operator_b", "reply_text": "Reply"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "operator_not_assigned"
    send_mock.assert_not_awaited()


@pytest.mark.story("04-runtime-config")
def test_epic04_runtime_config_overrides_default_operator(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setenv("HITL_TICKET_DB_PATH", hitl_ticket_repository.db_path)
    get_settings.cache_clear()
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))

    bot_client = TestClient(bot_app)
    config_response = bot_client.post(
        "/telegram/webhook",
        json={
            "update_id": 9100,
            "message": {
                "message_id": 1,
                "from": {"id": 1, "username": "ajdevy"},
                "chat": {"id": 1, "type": "private"},
                "text": "/hitl_config @runtime_op 999",
            },
        },
    )
    assert config_response.json()["status"] == "configured"

    api_client = TestClient(api_app)
    blocked = api_client.post("/suggest", json={"text": "Need help."}).json()
    assert blocked["hitl_operator_username"] == "@runtime_op"

    tickets = api_client.get("/hitl/tickets").json()["items"]
    assert tickets[0]["operator_username"] == "@runtime_op"

    get_settings.cache_clear()
