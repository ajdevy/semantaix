import sqlite3

from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    knowledge_candidate_repository,
    knowledge_moderation_repository,
)


def _seed_transcripts(path: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, role, text, trace_id, created_at
            )
            VALUES
                (1, 100, 'user', 'Hello', 't1', '2026-01-01T00:00:00Z'),
                (
                    1, 101, 'user', 'Reset password via settings and email token.',
                    't2', '2026-01-01T00:00:01Z'
                ),
                (
                    2, 200, 'user', 'Billing cycle is monthly with invoice on day one.',
                    't3', '2026-01-01T00:00:02Z'
                )
            """
        )


def test_knowledge_extract_mixed_transcript(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    _seed_transcripts(transcript_path)

    knowledge_candidate_repository.transcript_db_path = transcript_path
    knowledge_candidate_repository.db_path = knowledge_path
    knowledge_moderation_repository.db_path = knowledge_path
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    response = client.post("/knowledge/extract", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted_candidates"] == 2
    assert len(payload["items"]) == 2
    assert all(len(item["candidate_text"]) >= 20 for item in payload["items"])


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
