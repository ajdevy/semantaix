from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    knowledge_candidate_repository,
    knowledge_moderation_repository,
)
from tests.e2e.db_seed import seed_transcript_messages


def test_knowledge_extract_mixed_transcript(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    seed_transcript_messages(transcript_path)

    knowledge_candidate_repository.transcript_db_path = transcript_path
    knowledge_candidate_repository.db_path = knowledge_path
    knowledge_moderation_repository.db_path = knowledge_path
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    response = client.post("/knowledge/extract", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_candidates"] == 2
    assert payload["enqueued_for_moderation"] == 2
    assert len(payload["moderation_queue_ids"]) == 2
    assert len(payload["items"]) == 2
    assert all(len(item["candidate_text"]) >= 20 for item in payload["items"])

    extract_ids = {item["id"] for item in payload["items"]}
    pending = client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    assert len(pending) == 2
    linked = {item["source_extraction_candidate_id"] for item in pending}
    assert linked == extract_ids


def test_extract_idempotent_second_pass_does_not_enqueue(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    seed_transcript_messages(transcript_path)

    knowledge_candidate_repository.transcript_db_path = transcript_path
    knowledge_candidate_repository.db_path = knowledge_path
    knowledge_moderation_repository.db_path = knowledge_path
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    assert client.post("/knowledge/extract", json={}).status_code == 200
    pending_len = len(
        client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"]
    )

    payload = client.post("/knowledge/extract", json={}).json()
    assert payload["inserted_candidates"] == 0
    assert payload["enqueued_for_moderation"] == 0
    assert payload["moderation_queue_ids"] == []
    assert (
        len(client.get("/knowledge/candidates", params={"status": "pending"}).json()["items"])
        == pending_len
    )


def test_knowledge_extract_failure_emits_incident(tmp_path):
    knowledge_candidate_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    knowledge_candidate_repository.transcript_db_path = str(tmp_path / "missing.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    response = client.post("/knowledge/extract", json={})
    assert response.status_code == 500
    assert response.json()["detail"] == "knowledge_extraction_failed"
    incidents = client.get("/incidents/knowledge_extraction_failures").json()["items"]
    assert len(incidents) == 1
