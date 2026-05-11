from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.bot_gateway.app import main as bot_main
from services.bot_gateway.app.main import api_client, hitl_ticket_repository
from services.bot_gateway.app.main import app as bot_app


@pytest.fixture(autouse=True)
def _isolate_hitl(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    persistence_path = tmp_path / "persistence.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(persistence_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _customer_payload(text: str = "Когда придёт возврат?") -> dict:
    return {
        "update_id": 7001,
        "message": {
            "message_id": 1,
            "from": {"id": 9001, "username": "customer"},
            "chat": {"id": 9001, "type": "private"},
            "text": text,
        },
    }


def _operator_payload(
    *,
    text: str,
    reply_to_text: str | None,
    username: str = "operator",
) -> dict:
    msg: dict = {
        "message_id": 99,
        "from": {"id": 1, "username": username},
        "chat": {"id": 1, "type": "private"},
        "text": text,
    }
    if reply_to_text is not None:
        msg["reply_to_message"] = {"text": reply_to_text}
    return {"update_id": 7100, "message": msg}


def test_customer_message_is_forwarded_to_api_inbound(monkeypatch):
    forward = AsyncMock(return_value={"escalated": True})
    monkeypatch.setattr(api_client, "forward_inbound", forward)

    client = TestClient(bot_app)
    response = client.post("/telegram/webhook", json=_customer_payload())
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

    forward.assert_awaited_once()
    kwargs = forward.await_args.kwargs
    assert kwargs["text"] == "Когда придёт возврат?"
    assert kwargs["chat_id"] == 9001
    assert kwargs["customer_username"] == "@customer"
    assert isinstance(kwargs["trace_id"], str) and kwargs["trace_id"]


def test_customer_forward_failure_returns_200_with_failure_marker(monkeypatch):
    monkeypatch.setattr(
        api_client, "forward_inbound", AsyncMock(side_effect=RuntimeError("boom"))
    )
    client = TestClient(bot_app)
    response = client.post("/telegram/webhook", json=_customer_payload())
    assert response.status_code == 200
    assert response.json()["forward"] == "failed"


def test_operator_reply_with_ticket_quote_routes_to_api(monkeypatch):
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@operator",
        updated_by="@ajdevy",
    )
    monkeypatch.setattr(api_client, "forward_inbound", AsyncMock(return_value={}))
    deliver = AsyncMock(return_value={"delivered": True, "resolved": True})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="В течение 5 рабочих дней.",
            reply_to_text="HITL ticket #42 | from @customer | Когда возврат?",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "operator_reply_delivered"
    assert body["ticket_id"] == "42"
    deliver.assert_awaited_once_with(
        ticket_id=42,
        operator_username="@operator",
        reply_text="В течение 5 рабочих дней.",
    )


def test_operator_reply_without_quote_uses_single_open_ticket(monkeypatch):
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@operator",
        updated_by="@ajdevy",
    )
    ticket = hitl_ticket_repository.create(
        conversation_ref="q", reason="awaiting_human_response", target_chat_id=9001
    )
    hitl_ticket_repository.assign(ticket_id=ticket.id, operator_username="@operator")
    deliver = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="ответ оператора", reply_to_text=None),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "operator_reply_delivered"
    deliver.assert_awaited_once_with(
        ticket_id=ticket.id,
        operator_username="@operator",
        reply_text="ответ оператора",
    )


def test_operator_reply_without_quote_and_multiple_tickets_is_ignored(monkeypatch):
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@operator",
        updated_by="@ajdevy",
    )
    for i in range(2):
        t = hitl_ticket_repository.create(
            conversation_ref=f"q{i}", reason="r", target_chat_id=1000 + i
        )
        hitl_ticket_repository.assign(ticket_id=t.id, operator_username="@operator")
    deliver = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="который из ответов?", reply_to_text=None),
    )
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "operator_reply_unmatched"
    deliver.assert_not_awaited()


def test_operator_admin_command_takes_precedence_over_reply_branch(monkeypatch):
    # Even when the sender matches the configured operator, a /hitl_config
    # command must still be handled by the admin branch.
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@ajdevy",
        updated_by="@ajdevy",
    )
    deliver = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="/hitl_config @new_op 555",
            reply_to_text=None,
            username="ajdevy",
        ),
    )
    assert response.json()["status"] == "configured"
    deliver.assert_not_awaited()


def test_operator_reply_api_failure_is_reported(monkeypatch):
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@operator",
        updated_by="@ajdevy",
    )
    monkeypatch.setattr(
        api_client,
        "deliver_operator_reply",
        AsyncMock(side_effect=RuntimeError("api down")),
    )

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="answer",
            reply_to_text="HITL ticket #7 | from @c | q",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["reason"] == "operator_reply_delivery_failed"


def test_non_operator_message_is_not_routed_to_reply_branch(monkeypatch):
    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@operator",
        updated_by="@ajdevy",
    )
    deliver = AsyncMock(return_value={})
    forward = AsyncMock(return_value={})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)
    monkeypatch.setattr(api_client, "forward_inbound", forward)

    client = TestClient(bot_app)
    response = client.post("/telegram/webhook", json=_customer_payload())
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    deliver.assert_not_awaited()
    forward.assert_awaited_once()


def test_extract_ticket_id_handles_various_formats():
    assert bot_main._extract_ticket_id("HITL ticket #42 | x") == 42
    assert bot_main._extract_ticket_id("hitl ticket  #007") == 7
    assert bot_main._extract_ticket_id(None) is None
    assert bot_main._extract_ticket_id("no ticket here") is None
