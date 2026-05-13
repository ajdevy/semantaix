from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.bot_gateway.app.main import (
    api_client,
    hitl_ticket_repository,
    telegram_bot_sender,
)
from services.bot_gateway.app.main import app as bot_app

_PERSONA_PROMPT_FIXTURE = (
    "📝 Как нас будут звать? Ответьте на это сообщение в формате: "
    "«Имя Фамилия»"
)


@pytest.fixture(autouse=True)
def _isolated_bot_gateway(tmp_path, monkeypatch):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    persistence_path = tmp_path / "persistence.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(persistence_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _operator_payload(
    *,
    text: str,
    reply_to_text: str | None = None,
    username: str = "ajdevy",
    update_id: int = 5001,
) -> dict:
    msg: dict = {
        "message_id": 1,
        "from": {"id": 1, "username": username},
        "chat": {"id": 1, "type": "private"},
        "text": text,
    }
    if reply_to_text is not None:
        msg["reply_to_message"] = {"text": reply_to_text}
    return {"update_id": update_id, "message": msg}


def test_persona_command_oneshot_calls_api_and_sends_confirmation(monkeypatch):
    set_persona = AsyncMock(
        return_value={"first_name": "Мария", "last_name": "Петрова", "full_name": "Мария Петрова"}
    )
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona Мария Петрова"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "persona_updated"
    assert body["first_name"] == "Мария"
    assert body["last_name"] == "Петрова"
    set_persona.assert_awaited_once_with(
        first_name="Мария", last_name="Петрова", updated_by="@ajdevy"
    )
    send.assert_awaited_once()
    assert "Мария Петрова" in send.await_args.kwargs["text"]


def test_persona_command_without_args_sends_dialog_prompt(monkeypatch):
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "persona_prompt_sent"
    set_persona.assert_not_awaited()
    send.assert_awaited_once()
    assert "Как нас будут звать?" in send.await_args.kwargs["text"]


def test_persona_natural_trigger_sends_dialog_prompt(monkeypatch):
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    for trigger in ("смени имя", "Поменяй имя", "переименуй", "новое имя"):
        send.reset_mock()
        response = client.post(
            "/telegram/webhook",
            json=_operator_payload(text=trigger, update_id=hash(trigger) & 0x7FFFFFFF),
        )
        assert response.json()["status"] == "persona_prompt_sent", trigger
        send.assert_awaited_once()
        assert "Как нас будут звать?" in send.await_args.kwargs["text"]
    set_persona.assert_not_awaited()


def test_persona_reply_to_prompt_applies_new_persona(monkeypatch):
    set_persona = AsyncMock(
        return_value={"first_name": "Иван", "last_name": "Сидоров"}
    )
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="Иван Сидоров",
            reply_to_text=_PERSONA_PROMPT_FIXTURE,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "persona_updated"
    set_persona.assert_awaited_once_with(
        first_name="Иван", last_name="Сидоров", updated_by="@ajdevy"
    )


def test_persona_reply_with_malformed_answer_reprompts(monkeypatch):
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="ТолькоИмя",
            reply_to_text=_PERSONA_PROMPT_FIXTURE,
        ),
    )
    assert response.json()["status"] == "persona_invalid_reply"
    set_persona.assert_not_awaited()
    send.assert_awaited_once()
    assert "Не разобрал" in send.await_args.kwargs["text"]


def test_persona_non_admin_caller_is_ignored(monkeypatch):
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona Анна Иванова", username="random_user"),
    )
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "unauthorized_persona"
    set_persona.assert_not_awaited()
    send.assert_not_awaited()


def test_persona_non_admin_replying_to_prompt_is_ignored(monkeypatch):
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="Анна Иванова",
            reply_to_text="📝 Как нас будут звать?",
            username="random_user",
        ),
    )
    assert response.json()["reason"] == "unauthorized_persona"
    set_persona.assert_not_awaited()


def test_persona_api_failure_reports_status_to_operator(monkeypatch):
    monkeypatch.setattr(
        api_client,
        "set_persona",
        AsyncMock(side_effect=RuntimeError("api down")),
    )
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona Анна Иванова"),
    )
    assert response.json()["status"] == "persona_update_failed"
    send.assert_awaited_once()
    assert "не получилось" in send.await_args.kwargs["text"].lower()


def test_persona_command_with_partial_args_falls_to_dialog(monkeypatch):
    """/persona OnlyOneArg → prompt dialog (we don't try to guess)."""
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    send = AsyncMock(return_value=42)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send)

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona Анна"),
    )
    assert response.json()["status"] == "persona_prompt_sent"
    set_persona.assert_not_awaited()


def test_persona_send_swallows_missing_bot_token(monkeypatch):
    """If telegram_bot_sender.send_message raises (no token), webhook still succeeds."""
    set_persona = AsyncMock(return_value={"first_name": "Анна", "last_name": "Иванова"})
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    monkeypatch.setattr(
        telegram_bot_sender,
        "send_message",
        AsyncMock(side_effect=RuntimeError("missing_bot_token")),
    )

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(text="/persona Анна Иванова"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "persona_updated"


def test_persona_trigger_does_not_hijack_unrelated_operator_reply(monkeypatch):
    """An operator reply that doesn't start with a persona trigger and isn't
    a reply to the persona marker must NOT enter the persona branch."""
    set_persona = AsyncMock()
    monkeypatch.setattr(api_client, "set_persona", set_persona)
    deliver = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(api_client, "deliver_operator_reply", deliver)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock())

    hitl_ticket_repository.set_runtime_config(
        key="hitl_primary_operator_username",
        value="@ajdevy",
        updated_by="@ajdevy",
    )

    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_payload(
            text="ответ оператора",
            reply_to_text="HITL ticket #5 | from @c | q",
        ),
    )
    assert response.json()["status"] == "operator_reply_delivered"
    set_persona.assert_not_awaited()
