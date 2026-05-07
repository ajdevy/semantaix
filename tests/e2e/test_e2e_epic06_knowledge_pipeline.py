"""Epic 06: transcript extract → moderation queue → approve → RAG retrieval."""

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


def test_epic06_extract_approve_then_retrievable(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    seed_transcript_messages(transcript_path)

    knowledge_candidate_repository.transcript_db_path = transcript_path
    knowledge_candidate_repository.db_path = knowledge_path
    knowledge_moderation_repository.db_path = knowledge_path
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
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
