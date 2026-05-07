from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.main import (
    answer_trace_repository,
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    rag_repository,
)
from services.api.app.main import (
    app as api_app,
)


def _wire(tmp_path) -> None:
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")


def test_suggest_persists_trace_for_valid_response(tmp_path, monkeypatch):
    _wire(tmp_path)
    rag_repository.ingest(source_id="kb", text="reset password using email token")
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)

    response = client.post(
        "/suggest",
        json={"text": "reset password help", "trace_id": "trace-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == "trace-1"

    fetch = client.get("/answer-traces/trace-1")
    assert fetch.status_code == 200
    trace = fetch.json()
    assert trace["response_mode"] == "suggestion_only"
    assert trace["guardrail_outcome"] == "valid"
    assert trace["grounded"] is True
    assert trace["retrieval"][0]["source_ref"] == "kb"
    assert trace["model_provider"] == "openrouter"
    assert trace["latency_ms"] >= 0


def test_suggest_persists_trace_for_blocked_response(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))
    client = TestClient(api_app)

    response = client.post(
        "/suggest",
        json={"text": "Hard question", "trace_id": "trace-blocked"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == "trace-blocked"

    trace = client.get("/answer-traces/trace-blocked").json()
    assert trace["response_mode"] == "blocked_invalid"
    assert trace["guardrail_outcome"] == "invalid"
    assert "policy_blocked" in trace["limitations"]


def test_suggest_records_partial_context_when_no_retrieval(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)

    response = client.post(
        "/suggest",
        json={"text": "Question with no kb match", "trace_id": "trace-empty"},
    )
    assert response.status_code == 200
    trace = client.get("/answer-traces/trace-empty").json()
    assert trace["no_retrieval_hit"] is True
    assert trace["grounded"] is False
    assert "partial_context" in trace["limitations"]


def test_suggest_generates_trace_id_when_omitted(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "auto-id case"}).json()
    assert isinstance(response["trace_id"], str)
    assert len(response["trace_id"]) >= 32


def test_suggest_idempotent_on_duplicate_trace_id(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)
    first = client.post("/suggest", json={"text": "ping", "trace_id": "shared"}).json()
    second = client.post("/suggest", json={"text": "ping", "trace_id": "shared"}).json()
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
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)
    client.post("/suggest", json={"text": "ping", "trace_id": "trace-A"})
    client.post("/suggest", json={"text": "ping", "trace_id": "trace-B"})
    listed = client.get("/answer-traces").json()["items"]
    ids_in_order = [item["trace_id"] for item in listed[:2]]
    assert ids_in_order == ["trace-B", "trace-A"]


def test_suggest_emits_incident_when_trace_persistence_fails(tmp_path, monkeypatch):
    _wire(tmp_path)
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))

    def _explode(**_kwargs):
        raise RuntimeError("storage offline")

    monkeypatch.setattr(answer_trace_repository, "write", _explode)
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "ping", "trace_id": "trace-fail"})
    assert response.status_code == 200
    assert response.json()["trace_id"] is None
    incidents = client.get("/incidents/answer_trace_persistence_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"
