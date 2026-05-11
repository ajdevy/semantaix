"""Epic 08 Story 04: trace -> correction -> moderation approval -> retrievable."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.answerers import AnswerResult
from services.api.app.main import (
    answer_pipeline,
    answer_trace_repository,
    hitl_ticket_repository,
    incident_repository,
    knowledge_moderation_repository,
    rag_repository,
    telegram_bot_sender,
    trace_correction_repository,
)
from services.api.app.main import app as api_app

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-04")]


def test_epic08_trace_correction_to_moderation_then_approved_retrievable(tmp_path, monkeypatch):
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    trace_correction_repository.db_path = str(tmp_path / "nl_ops.sqlite3")
    monkeypatch.setattr(telegram_bot_sender, "send_message", AsyncMock(return_value=1))
    monkeypatch.setattr(
        answer_pipeline,
        "run",
        AsyncMock(
            return_value=AnswerResult(
                handled=True,
                text="Use reset flow.",
                response_mode="grounded_rag",
                metadata={"answerer": "grounded_rag", "guardrail_score": 0.95},
            )
        ),
    )

    client = TestClient(api_app)
    seed = client.post(
        "/conversations/inbound",
        json={"text": "reset password help", "trace_id": "epic08-correct"},
    )
    assert seed.status_code == 200

    correction_text = "Reset link is found in the email under settings menu."
    submit = client.post(
        "/answer-traces/epic08-correct/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": correction_text,
            "branch": "moderation",
        },
    )
    assert submit.status_code == 200
    candidate_id = submit.json()["candidate_id"]

    approve = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert approve.status_code == 200

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset link email settings", "limit": 5},
    )
    assert retrieve.status_code == 200
    sources = [item["source_id"] for item in retrieve.json()["items"]]
    assert any(src.startswith("knowledge_candidate:") for src in sources)

    audit = client.get("/answer-traces/epic08-correct/audit").json()["items"]
    op_types = {entry["op_type"] for entry in audit}
    assert "correction_pending_moderation" in op_types
