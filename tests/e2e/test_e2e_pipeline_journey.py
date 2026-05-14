"""Russian-first inbound pipeline e2e: deterministic -> grounded -> HITL."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.answerers.weather_client import WeatherSummary
from services.api.app.main import (
    answer_trace_repository,
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    rag_repository,
    settings,
    telegram_bot_sender,
    weather_client,
)
from services.api.app.main import app as api_app
from services.api.app.openrouter_client import GroundingVerdict
from services.bot_gateway.app.main import (
    api_client as bot_api_client,
)
from services.bot_gateway.app.main import app as bot_app
from services.bot_gateway.app.main import (
    hitl_ticket_repository as bot_hitl_repo,
)

pytestmark = [pytest.mark.e2e, pytest.mark.epic("pipeline")]


def _wire(tmp_path, monkeypatch):
    hitl_path = str(tmp_path / "hitl.sqlite3")
    hitl_ticket_repository.db_path = hitl_path
    bot_hitl_repo.db_path = hitl_path
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")
    # Isolate the bot_gateway persistence DB too: the gateway now
    # short-circuits on duplicate source_message_id, so leaking rows from
    # earlier test runs would make the first webhook in this test appear
    # as a duplicate. We monkeypatch the attribute on the cached settings
    # singleton rather than setenv+cache_clear, because clearing the
    # lru_cache breaks downstream tests that monkeypatch get_settings()
    # (which would return a different instance from the api/main module's
    # module-level ``settings`` binding).
    monkeypatch.setattr(
        settings, "persistence_db_path", str(tmp_path / "persistence.sqlite3")
    )
    monkeypatch.setattr(settings, "hitl_primary_operator_username", "@operator")


def _post_inbound(client, **kwargs):
    return client.post("/conversations/inbound", json=kwargs).json()


def test_e2e_deterministic_date_question_no_hitl(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    send_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(telegram_bot_sender, "send_message", send_mock)
    client = TestClient(api_app)

    body = _post_inbound(
        client,
        text="Какое сегодня число?",
        chat_id=9001,
        customer_username="@customer",
        trace_id="t-det-date",
    )
    assert body["delivered"] is True
    assert body["escalated"] is False
    assert body["response_mode"] == "deterministic_datetime"
    assert body["answerer"] == "datetime"

    # No HITL ticket created for deterministic answers
    assert client.get("/hitl/tickets").json()["items"] == []
    send_mock.assert_awaited()


def test_e2e_holiday_answer_with_country_runtime_override(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    # Default country is RU; verify override works too.
    hitl_ticket_repository.set_runtime_config(
        key="default_country_code", value="RU", updated_by="@ajdevy"
    )
    client = TestClient(api_app)
    body = _post_inbound(
        client,
        text="Какой следующий праздник?",
        chat_id=9001,
        trace_id="t-holiday",
    )
    assert body["response_mode"] == "deterministic_holiday"


def test_e2e_weather_cyrillic_city_via_open_meteo_mock(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    monkeypatch.setattr(
        weather_client,
        "fetch",
        AsyncMock(
            return_value=WeatherSummary(
                location_name="Moscow",
                temperature_c=15.0,
                condition_ru="переменная облачность",
                condition_en="partly cloudy",
            )
        ),
    )
    client = TestClient(api_app)
    body = _post_inbound(
        client, text="Какая погода в Москве?", chat_id=9001, trace_id="t-weather"
    )
    assert body["response_mode"] == "deterministic_weather"
    assert "Moscow" in body["answer_text"]


def test_e2e_grounded_rag_russian_answer(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    hitl_ticket_repository.set_runtime_config(
        key="rag_grounding_score_threshold", value="0.2", updated_by="@admin"
    )
    rag_repository.ingest(
        source_id="kb-refunds",
        text="Возврат денег занимает пять рабочих дней",
    )
    monkeypatch.setattr(
        openrouter_client,
        "answer_grounded",
        AsyncMock(return_value="Возврат денег занимает пять рабочих дней."),
    )
    monkeypatch.setattr(
        openrouter_client,
        "verify_grounding",
        AsyncMock(
            return_value=GroundingVerdict(label="GROUNDED", reason="matches snippet")
        ),
    )

    client = TestClient(api_app)
    body = _post_inbound(
        client,
        text="когда придёт мой возврат?",
        chat_id=9001,
        trace_id="t-grounded",
    )
    assert body["response_mode"] == "grounded_rag"
    assert "пять рабочих дней" in body["answer_text"]


def test_e2e_full_hitl_journey_via_bot_gateway(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    # Force escalation: no RAG corpus, no deterministic match
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))

    api_client = TestClient(api_app)

    # 1. Customer sends a free-form question through bot_gateway webhook
    bot_client = TestClient(bot_app)
    customer_payload = {
        "update_id": 8001,
        "message": {
            "message_id": 1,
            "from": {"id": 9001, "username": "customer"},
            "chat": {"id": 9001, "type": "private"},
            "text": "Когда придёт мой возврат?",
        },
    }
    # The bot_gateway will try to forward to api over httpx — short-circuit
    # by patching api_client.forward_inbound to call the api app directly.
    async def _forward(*, text, chat_id, customer_username, trace_id):
        return api_client.post(
            "/conversations/inbound",
            json={
                "text": text,
                "chat_id": chat_id,
                "customer_username": customer_username,
                "trace_id": trace_id,
            },
        ).json()

    monkeypatch.setattr(bot_api_client, "forward_inbound", _forward)
    webhook = bot_client.post("/telegram/webhook", json=customer_payload)
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "accepted"

    tickets = api_client.get("/hitl/tickets").json()["items"]
    assert len(tickets) == 1
    ticket_id = tickets[0]["id"]
    assert tickets[0]["target_chat_id"] == 9001
    assert tickets[0]["operator_username"] == "@operator"
    assert tickets[0]["status"] == "assigned"

    # 2. Operator sends a Telegram reply quoting the ticket DM. Bot_gateway
    # routes via api_client.deliver_operator_reply -> /hitl/tickets/{id}/reply.
    async def _deliver(*, ticket_id, operator_username, reply_text):
        return api_client.post(
            f"/hitl/tickets/{ticket_id}/reply",
            json={
                "operator_username": operator_username,
                "reply_text": reply_text,
            },
        ).json()

    monkeypatch.setattr(bot_api_client, "deliver_operator_reply", _deliver)

    operator_payload = {
        "update_id": 8002,
        "message": {
            "message_id": 2,
            "from": {"id": 1, "username": "operator"},
            "chat": {"id": 1, "type": "private"},
            "text": "В течение 5 рабочих дней.",
            "reply_to_message": {
                "text": f"HITL ticket #{ticket_id} | from @customer | Когда возврат?"
            },
        },
    }
    op_resp = bot_client.post("/telegram/webhook", json=operator_payload)
    assert op_resp.status_code == 200
    assert op_resp.json()["status"] == "operator_reply_delivered"

    # 3. Ticket auto-resolved
    final = hitl_ticket_repository.get(ticket_id)
    assert final.status == "resolved"
    assert final.resolved_at is not None


def test_e2e_slang_intent_via_normalization(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    client = TestClient(api_app)
    body = _post_inbound(client, text="че по времени?", chat_id=9001, trace_id="t-slang")
    assert body["response_mode"] == "deterministic_datetime"


def test_e2e_slang_rag_via_lemma_overlap(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    hitl_ticket_repository.set_runtime_config(
        key="rag_grounding_score_threshold", value="0.2", updated_by="@admin"
    )
    rag_repository.ingest(
        source_id="kb-money",
        text="Возврат денег занимает пять рабочих дней",
    )
    monkeypatch.setattr(
        openrouter_client,
        "answer_grounded",
        AsyncMock(return_value="Деньги вернутся за пять рабочих дней."),
    )
    monkeypatch.setattr(
        openrouter_client,
        "verify_grounding",
        AsyncMock(return_value=GroundingVerdict(label="GROUNDED", reason="ok")),
    )
    client = TestClient(api_app)
    # "бабло" -> "деньги" -> lemma overlap with "денег" chunk
    body = _post_inbound(client, text="когда придёт бабло?", chat_id=9001)
    assert body["response_mode"] == "grounded_rag"


def test_e2e_profanity_in_llm_output_escalates(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    hitl_ticket_repository.set_runtime_config(
        key="rag_grounding_score_threshold", value="0.2", updated_by="@admin"
    )
    rag_repository.ingest(
        source_id="kb-x",
        text="Возврат денег занимает пять рабочих дней",
    )
    monkeypatch.setattr(
        openrouter_client,
        "answer_grounded",
        AsyncMock(return_value="Полный пиздец с возвратами в эти дни."),
    )
    monkeypatch.setattr(
        openrouter_client,
        "verify_grounding",
        AsyncMock(return_value=GroundingVerdict(label="GROUNDED", reason="ok")),
    )
    client = TestClient(api_app)
    body = _post_inbound(
        client, text="когда придёт возврат?", chat_id=9001, trace_id="t-profane"
    )
    assert body["escalated"] is True
    assert body["response_mode"] == "human_only"


def test_e2e_english_question_still_works_bilingual(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    client = TestClient(api_app)
    body = _post_inbound(client, text="what is the date?", chat_id=9001)
    assert body["response_mode"] == "deterministic_datetime"
