from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    knowledge_moderation_repository,
    rag_repository,
)


def test_create_list_approve_reindexes_and_retrievable(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    created = client.post(
        "/knowledge/candidates",
        json={"text": "Reset passwords from account settings using the email link."},
    )
    assert created.status_code == 200
    candidate_id = created.json()["id"]

    listed = client.get("/knowledge/candidates", params={"status": "pending"})
    assert len(listed.json()["items"]) == 1

    approve = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": None},
    )
    assert approve.status_code == 200
    payload = approve.json()
    assert payload["status"] == "approved"
    assert payload["inserted_chunks"] >= 1
    source_id = f"knowledge_candidate:{candidate_id}"

    retrieval = client.post(
        "/rag/retrieve",
        json={"query": "reset passwords account settings email", "limit": 5},
    )
    assert retrieval.status_code == 200
    sources = [item["source_id"] for item in retrieval.json()["items"]]
    assert source_id in sources


def test_reject_does_not_index(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    created = client.post(
        "/knowledge/candidates",
        json={"text": "Secret phrase only for rejected candidate path here."},
    )
    candidate_id = created.json()["id"]
    reject = client.post(f"/knowledge/candidates/{candidate_id}/reject")
    assert reject.status_code == 200

    retrieval = client.post(
        "/rag/retrieve",
        json={"query": "Secret phrase only for rejected", "limit": 5},
    )
    assert retrieval.json()["items"] == []


def test_approve_with_edit_uses_edited_text(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    created = client.post(
        "/knowledge/candidates",
        json={"text": "Original text for indexing in knowledge base."},
    )
    candidate_id = created.json()["id"]
    edited = "Edited guidance for monthly billing cycle and invoice timing."
    approve = client.post(
        f"/knowledge/candidates/{candidate_id}/approve",
        json={"edited_text": edited},
    )
    assert approve.status_code == 200
    assert approve.json()["published_text"] == edited

    retrieval = client.post(
        "/rag/retrieve",
        json={"query": "monthly billing invoice", "limit": 3},
    )
    texts = [item["chunk_text"] for item in retrieval.json()["items"]]
    assert any("monthly billing" in t.lower() for t in texts)


def test_double_approve_returns_conflict(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    created = client.post(
        "/knowledge/candidates",
        json={"text": "Another long candidate that can be approved once only."},
    )
    candidate_id = created.json()["id"]
    assert client.post(f"/knowledge/candidates/{candidate_id}/approve", json={}).status_code == 200
    second = client.post(f"/knowledge/candidates/{candidate_id}/approve", json={})
    assert second.status_code == 409
    assert second.json()["detail"] == "candidate_not_pending"


def test_reindex_failure_emits_incident(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    def _raise_ingest(**kwargs):
        raise RuntimeError("rag down")

    monkeypatch.setattr(rag_repository, "ingest", _raise_ingest)
    created = client.post(
        "/knowledge/candidates",
        json={"text": "Candidate text that should fail during reindex path."},
    )
    candidate_id = created.json()["id"]
    response = client.post(f"/knowledge/candidates/{candidate_id}/approve", json={})
    assert response.status_code == 500
    assert response.json()["detail"] == "knowledge_reindex_failed"
    incidents = client.get("/incidents/knowledge_reindex_failures").json()["items"]
    assert len(incidents) == 1


def test_create_empty_text_rejected(tmp_path):
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    client = TestClient(api_app)
    response = client.post("/knowledge/candidates", json={"text": "  "})
    assert response.status_code == 400
    assert response.json()["detail"] == "empty_candidate_text"


def test_approve_missing_candidate_returns_404(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    client = TestClient(api_app)
    response = client.post("/knowledge/candidates/99999/approve", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "candidate_not_found"


def test_approve_empty_publish_text_returns_400(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    candidate = knowledge_moderation_repository.create_pending(text="   ")
    client = TestClient(api_app)
    response = client.post(f"/knowledge/candidates/{candidate.id}/approve", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "empty_publish_text"


def test_approve_when_mark_approved_fails_returns_409(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = client.post(
        "/knowledge/candidates",
        json={"text": "Some text long enough so approve path runs ingest then mark fails."},
    )
    candidate_id = created.json()["id"]

    def _boom(**kwargs):
        raise ValueError("invalid_status")

    monkeypatch.setattr(knowledge_moderation_repository, "mark_approved", _boom)
    response = client.post(f"/knowledge/candidates/{candidate_id}/approve", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "candidate_not_pending"


def test_approve_when_mark_approved_missing_returns_404(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)
    created = client.post(
        "/knowledge/candidates",
        json={"text": "Different text length for ingest success then missing mark path coverage."},
    )
    candidate_id = created.json()["id"]

    def _boom(**kwargs):
        raise LookupError("candidate_not_found")

    monkeypatch.setattr(knowledge_moderation_repository, "mark_approved", _boom)
    response = client.post(f"/knowledge/candidates/{candidate_id}/approve", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "candidate_not_found"


def test_reject_missing_candidate_returns_404(tmp_path):
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    client = TestClient(api_app)
    response = client.post("/knowledge/candidates/424242/reject")
    assert response.status_code == 404
    assert response.json()["detail"] == "candidate_not_found"


def test_reject_already_final_returns_409(tmp_path):
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    client = TestClient(api_app)
    created = client.post(
        "/knowledge/candidates",
        json={"text": "Text for duplicate reject moderation path coverage."},
    )
    candidate_id = created.json()["id"]
    client.post(f"/knowledge/candidates/{candidate_id}/reject")
    second = client.post(f"/knowledge/candidates/{candidate_id}/reject")
    assert second.status_code == 409
    assert second.json()["detail"] == "candidate_not_pending"


def test_approve_unknown_value_error_maps_to_400(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")

    def _odd(**kwargs):
        raise ValueError("unexpected_prepare_reason")

    monkeypatch.setattr(knowledge_moderation_repository, "prepare_publish_text", _odd)
    client = TestClient(api_app)
    response = client.post("/knowledge/candidates/1/approve", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "unexpected_prepare_reason"


def test_reject_value_error_maps_to_400(tmp_path, monkeypatch):
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")

    def _odd(**kwargs):
        raise ValueError("custom_reject_problem")

    monkeypatch.setattr(knowledge_moderation_repository, "reject", _odd)
    client = TestClient(api_app)
    response = client.post("/knowledge/candidates/1/reject")
    assert response.status_code == 400
    assert response.json()["detail"] == "custom_reject_problem"


def test_list_candidates_without_status_param(tmp_path):
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    client = TestClient(api_app)
    client.post(
        "/knowledge/candidates",
        json={"text": "First bulk list candidate row for moderation."},
    )
    client.post(
        "/knowledge/candidates",
        json={"text": "Second bulk list candidate row for moderation."},
    )
    response = client.get("/knowledge/candidates")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
