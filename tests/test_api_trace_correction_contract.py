from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.answerers import AnswerResult
from services.api.app.main import (
    answer_pipeline,
    answer_trace_repository,
    incident_repository,
    knowledge_moderation_repository,
    rag_repository,
    telegram_bot_sender,
    trace_correction_repository,
)
from services.api.app.main import app as api_app


def _wire(tmp_path) -> None:
    answer_trace_repository.db_path = str(tmp_path / "answer_traces.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    trace_correction_repository.db_path = str(tmp_path / "nl_ops.sqlite3")


def _seed_trace(client: TestClient, monkeypatch, trace_id: str) -> None:
    monkeypatch.setattr(
        telegram_bot_sender, "send_message", AsyncMock(return_value=1)
    )
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
    response = client.post(
        "/conversations/inbound",
        json={"text": "reset password help", "trace_id": trace_id},
    )
    assert response.status_code == 200


def test_record_open_writes_audit(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-open")

    response = client.post(
        "/answer-traces/trace-open/open",
        json={"tenant_id": "org", "user_id": "u1"},
    )
    assert response.status_code == 200
    audit = client.get("/answer-traces/trace-open/audit").json()["items"]
    assert audit[0]["op_type"] == "trace_opened"


def test_record_open_unknown_trace_returns_404(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    response = client.post(
        "/answer-traces/missing/open",
        json={"tenant_id": "org", "user_id": "u1"},
    )
    assert response.status_code == 404


def test_record_open_blank_inputs_return_400(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-blank")
    response = client.post(
        "/answer-traces/trace-blank/open",
        json={"tenant_id": "", "user_id": ""},
    )
    assert response.status_code == 400


def test_submit_publish_correction_reindexes(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-pub")

    response = client.post(
        "/answer-traces/trace-pub/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "Reset link is found in the email link section.",
            "branch": "publish",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "published"
    assert body["source_id"] == "trace_correction:org:trace-pub"

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset link email section", "limit": 5},
    ).json()
    assert any(item["source_id"] == body["source_id"] for item in retrieve["items"])


def test_submit_moderation_correction_creates_candidate(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-mod")

    response = client.post(
        "/answer-traces/trace-mod/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "Pending review correction text",
            "branch": "moderation",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_moderation"
    assert body["candidate_id"] is not None
    pending = client.get(
        "/knowledge/candidates", params={"status": "pending"}
    ).json()["items"]
    assert any(item["id"] == body["candidate_id"] for item in pending)


def test_submit_correction_invalid_branch_returns_400(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-bad")
    response = client.post(
        "/answer-traces/trace-bad/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "x",
            "branch": "nope",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_branch"


def test_submit_correction_blank_text_returns_400(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-empty")
    response = client.post(
        "/answer-traces/trace-empty/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "  ",
            "branch": "publish",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "edited_text_required"


def test_submit_correction_unknown_trace_returns_404(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    response = client.post(
        "/answer-traces/missing/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "x",
            "branch": "publish",
        },
    )
    assert response.status_code == 404


def test_submit_publish_reindex_failure_emits_incident(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-fail")

    def _explode(*_args, **_kwargs):
        raise RuntimeError("rag offline")

    monkeypatch.setattr(rag_repository, "ingest", _explode)
    response = client.post(
        "/answer-traces/trace-fail/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "Will fail to reindex",
            "branch": "publish",
        },
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "trace_correction_reindex_failed"
    incidents = client.get("/incidents/trace_correction_reindex_failures").json()["items"]
    assert len(incidents) == 1


def test_list_corrections_returns_records(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-list")
    client.post(
        "/answer-traces/trace-list/corrections",
        json={
            "tenant_id": "org",
            "user_id": "u1",
            "edited_text": "Edit one",
            "branch": "publish",
        },
    )
    listing = client.get("/answer-traces/trace-list/corrections").json()["items"]
    assert len(listing) == 1
    assert listing[0]["status"] == "published"


def test_list_audit_filters_by_trace(tmp_path, monkeypatch):
    _wire(tmp_path)
    client = TestClient(api_app)
    _seed_trace(client, monkeypatch, "trace-audit-A")
    _seed_trace(client, monkeypatch, "trace-audit-B")
    client.post(
        "/answer-traces/trace-audit-A/open",
        json={"tenant_id": "org", "user_id": "u1"},
    )
    client.post(
        "/answer-traces/trace-audit-B/open",
        json={"tenant_id": "org", "user_id": "u1"},
    )
    audit_a = client.get("/answer-traces/trace-audit-A/audit").json()["items"]
    assert all(entry["details"].get("trace_id") == "trace-audit-A" for entry in audit_a)


def test_list_audit_unknown_trace_returns_404(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    response = client.get("/answer-traces/missing/audit")
    assert response.status_code == 404


def test_list_corrections_unknown_trace_returns_404(tmp_path):
    _wire(tmp_path)
    client = TestClient(api_app)
    response = client.get("/answer-traces/missing/corrections")
    assert response.status_code == 404
