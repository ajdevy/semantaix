"""Epic 08 Story 01: inbound emits a queryable answer_trace row."""

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
    telegram_bot_sender,
)
from services.api.app.main import app as api_app

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-01")]


def test_epic08_inbound_writes_queryable_trace(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    monkeypatch.setattr(
        answer_pipeline,
        "run",
        AsyncMock(
            return_value=AnswerResult(
                handled=True,
                text="Use the reset link.",
                response_mode="grounded_rag",
                metadata={
                    "retrieval": [
                        {
                            "chunk_id": "1",
                            "source_ref": "kb",
                            "score": 0.9,
                            "text_snippet": "reset password through the email link",
                        }
                    ],
                    "answerer": "grounded_rag",
                    "guardrail_score": 0.95,
                },
            )
        ),
    )
    client = TestClient(api_app)

    response = client.post(
        "/conversations/inbound",
        json={"text": "reset password help", "trace_id": "epic08-1"},
    )
    assert response.status_code == 200
    assert response.json()["trace_id"] == "epic08-1"

    fetched = client.get("/answer-traces/epic08-1").json()
    assert fetched["response_mode"] == "grounded_rag"
    assert fetched["guardrail_outcome"] == "valid"
    assert fetched["grounded"] is True
    assert fetched["retrieval"][0]["source_ref"] == "kb"
    assert fetched["model_provider"] == "openrouter"
