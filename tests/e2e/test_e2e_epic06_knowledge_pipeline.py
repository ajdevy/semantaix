"""Epic 06: transcript extract -> moderation queue -> approve/reject -> RAG retrieval."""

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    knowledge_candidate_repository,
    knowledge_moderation_repository,
    rag_repository,
)
from tests.e2e.db_seed import seed_transcript_messages

pytestmark = [pytest.mark.e2e, pytest.mark.epic("06"), pytest.mark.story("06-02")]


def _wire_pipeline(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    seed_transcript_messages(transcript_path)
    knowledge_candidate_repository.transcript_db_path = transcript_path
    knowledge_candidate_repository.db_path = knowledge_path
    knowledge_moderation_repository.db_path = knowledge_path
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300


def test_epic06_extract_approve_then_retrievable(tmp_path):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    extract = client.post("/knowledge/extract", json={})
    assert extract.status_code == 200
    body = extract.json()
    assert body["inserted_candidates"] == 2
    assert body["enqueued_for_moderation"] == 2

    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    assert len(pending) == 2
    candidate_id = pending[0]["id"]

    approved = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert approved.status_code == 200
    approve_body = approved.json()
    assert approve_body["status"] == "approved"
    source_id = f"knowledge_candidate:{candidate_id}"

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset password settings email", "limit": 5},
    )
    assert retrieve.status_code == 200
    sources = [item["source_id"] for item in retrieve.json()["items"]]
    assert source_id in sources


def test_epic06_extract_reject_path_not_retrievable(tmp_path):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    client.post("/knowledge/extract", json={})
    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    candidate = next(c for c in pending if "reset password" in c["candidate_text"].lower())

    response = client.post(f"/knowledge/candidates/{candidate['id']}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    rejected = client.get(
        "/knowledge/candidates",
        params={"status": "rejected"},
    ).json()["items"]
    assert any(item["id"] == candidate["id"] for item in rejected)

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset password settings email", "limit": 5},
    ).json()
    sources = [item["source_id"] for item in retrieve["items"]]
    assert f"knowledge_candidate:{candidate['id']}" not in sources


def test_epic06_approve_with_edited_text_publishes_edited_version(tmp_path):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    client.post("/knowledge/extract", json={})
    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    candidate_id = pending[0]["id"]

    response = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": "Edited canonical answer about password resets and email links."},
    )
    assert response.status_code == 200
    assert response.json()["published_text"].startswith("Edited canonical answer")

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "edited canonical answer", "limit": 5},
    ).json()
    matched = [
        item
        for item in retrieve["items"]
        if item["source_id"] == f"knowledge_candidate:{candidate_id}"
    ]
    assert matched
    assert "edited canonical" in matched[0]["chunk_text"].lower()


def test_epic06_extract_idempotent_second_pass_enqueues_zero(tmp_path):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    first = client.post("/knowledge/extract", json={}).json()
    second = client.post("/knowledge/extract", json={}).json()
    assert first["inserted_candidates"] == 2
    assert second["inserted_candidates"] == 0
    assert second["enqueued_for_moderation"] == 0


def test_epic06_double_approve_returns_409(tmp_path):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    client.post("/knowledge/extract", json={})
    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    candidate_id = pending[0]["id"]

    first = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert first.status_code == 200

    second = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "candidate_not_pending"


def test_epic06_reindex_failure_emits_incident(tmp_path, monkeypatch):
    _wire_pipeline(tmp_path)
    client = TestClient(api_app)

    client.post("/knowledge/extract", json={})
    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    candidate_id = pending[0]["id"]

    def _boom(**kwargs):
        raise RuntimeError("rag write failed")

    monkeypatch.setattr(rag_repository, "ingest", _boom)

    response = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "knowledge_reindex_failed"

    incidents = client.get("/incidents/knowledge_reindex_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"
