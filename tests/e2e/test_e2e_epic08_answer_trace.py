"""Epic 08 Story 01: /suggest emits a queryable answer_trace row."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import (
    answer_trace_repository,
    incident_repository,
    openrouter_client,
    rag_repository,
)
from services.api.app.main import (
    app as api_app,
)

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-01")]


def test_epic08_suggest_writes_queryable_trace(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")
    rag_repository.ingest(source_id="kb", text="reset password through the email link")
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use the reset link."))
    client = TestClient(api_app)

    suggest = client.post(
        "/suggest",
        json={"text": "reset password help", "trace_id": "epic08-1"},
    )
    assert suggest.status_code == 200
    assert suggest.json()["trace_id"] == "epic08-1"

    fetched = client.get("/answer-traces/epic08-1").json()
    assert fetched["response_mode"] == "suggestion_only"
    assert fetched["guardrail_outcome"] == "valid"
    assert fetched["grounded"] is True
    assert fetched["retrieval"][0]["source_ref"] == "kb"
    assert fetched["model_provider"] == "openrouter"
