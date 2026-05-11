from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.answerers import AnswerResult
from services.api.app.main import (
    answer_pipeline,
    answer_trace_repository,
    hitl_ticket_repository,
    incident_repository,
    rag_repository,
    telegram_bot_sender,
)
from services.api.app.main import app as api_app


def _wire(tmp_path) -> None:
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")


def _grounded_pipeline(monkeypatch, source_id: str = "kb"):
    monkeypatch.setattr(
        answer_pipeline,
        "run",
        AsyncMock(
            return_value=AnswerResult(
                handled=True,
                text="Use reset flow.",
                response_mode="grounded_rag",
                metadata={
                    "retrieval": [
                        {
                            "chunk_id": "1",
                            "source_ref": source_id,
                            "score": 0.9,
                            "text_snippet": "reset password using email token",
                        }
                    ],
                    "answerer": "grounded_rag",
                    "guardrail_score": 0.95,
                },
            )
        ),
    )


def _deterministic_pipeline(monkeypatch):
    monkeypatch.setattr(
        answer_pipeline,
        "run",
        AsyncMock(
            return_value=AnswerResult(
                handled=True,
                text="Сейчас 14:32.",
                response_mode="deterministic_datetime",
                metadata={"answerer": "datetime"},
            )
        ),
    )


def _escalated_pipeline(monkeypatch):
    monkeypatch.setattr(
        answer_pipeline, "run", AsyncMock(return_value=AnswerResult(handled=False))
    )


def test_inbound_persists_trace_for_grounded_response(tmp_path, monkeypatch):
    _wire(tmp_path)
    _grounded_pipeline(monkeypatch)
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound",
        json={"text": "reset password help", "trace_id": "trace-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == "trace-1"

    trace = client.get("/answer-traces/trace-1").json()
    assert trace["response_mode"] == "grounded_rag"
    assert trace["guardrail_outcome"] == "valid"
    assert trace["grounded"] is True
    assert trace["retrieval"][0]["source_ref"] == "kb"


def test_inbound_persists_trace_for_escalated_response(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _escalated_pipeline(monkeypatch)
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound",
        json={"text": "Когда придёт возврат?", "trace_id": "trace-escalated"},
    )
    assert response.status_code == 200

    trace = client.get("/answer-traces/trace-escalated").json()
    assert trace["response_mode"] == "human_only"
    assert trace["guardrail_outcome"] == "escalated"
    assert "awaiting_human_response" in trace["limitations"]


def test_inbound_records_no_retrieval_for_deterministic_answer(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _deterministic_pipeline(monkeypatch)
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound",
        json={"text": "Какое сегодня число?", "trace_id": "trace-det"},
    )
    assert response.status_code == 200
    trace = client.get("/answer-traces/trace-det").json()
    assert trace["no_retrieval_hit"] is True
    assert trace["grounded"] is False
    assert "no_retrieval" in trace["limitations"]


def test_inbound_generates_trace_id_when_omitted(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _deterministic_pipeline(monkeypatch)
    client = TestClient(api_app)
    response = client.post("/conversations/inbound", json={"text": "anything"}).json()
    assert isinstance(response["trace_id"], str)
    assert len(response["trace_id"]) >= 32


def test_inbound_idempotent_on_duplicate_trace_id(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _deterministic_pipeline(monkeypatch)
    client = TestClient(api_app)
    first = client.post(
        "/conversations/inbound", json={"text": "ping", "trace_id": "shared"}
    ).json()
    second = client.post(
        "/conversations/inbound", json={"text": "ping", "trace_id": "shared"}
    ).json()
    assert first["trace_id"] == second["trace_id"] == "shared"
    listed = client.get("/answer-traces", params={"limit": 50}).json()["items"]
    matching = [item for item in listed if item["trace_id"] == "shared"]
    assert len(matching) == 1


def test_get_answer_trace_returns_404_for_unknown(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    response = client.get("/answer-traces/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == "answer_trace_not_found"


def test_list_answer_traces_returns_recent_first(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _deterministic_pipeline(monkeypatch)
    client = TestClient(api_app)
    client.post("/conversations/inbound", json={"text": "ping", "trace_id": "trace-A"})
    client.post("/conversations/inbound", json={"text": "ping", "trace_id": "trace-B"})
    listed = client.get("/answer-traces").json()["items"]
    ids_in_order = [item["trace_id"] for item in listed[:2]]
    assert ids_in_order == ["trace-B", "trace-A"]


def test_inbound_emits_incident_when_trace_persistence_fails(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
    _deterministic_pipeline(monkeypatch)

    def _explode(**_kwargs):
        raise RuntimeError("storage offline")

    monkeypatch.setattr(answer_trace_repository, "write", _explode)
    client = TestClient(api_app)
    response = client.post(
        "/conversations/inbound", json={"text": "ping", "trace_id": "trace-fail"}
    )
    assert response.status_code == 200
    assert response.json()["trace_id"] is None
    incidents = client.get("/incidents/answer_trace_persistence_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"
