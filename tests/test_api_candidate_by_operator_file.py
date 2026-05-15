"""Contract tests for GET /knowledge/candidates/by-operator-file/{short_id}."""

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.knowledge_moderation import KnowledgeModerationRepository


@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    repo = KnowledgeModerationRepository(str(tmp_path / "km.sqlite3"))
    monkeypatch.setattr(api_main, "knowledge_moderation_repository", repo)
    return repo


def test_lookup_returns_candidate(isolated_knowledge):
    candidate = isolated_knowledge.create_approved_operator_upload(
        candidate_text="hello",
        published_text="hello",
        operator_username="@op",
        is_confidential=False,
        source_file_name="doc.txt",
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
        operator_short_id="ABC123",
    )
    client = TestClient(api_main.app)
    response = client.get("/knowledge/candidates/by-operator-file/ABC123")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == candidate.id
    assert body["operator_short_id"] == "ABC123"


def test_lookup_unknown_short_id_returns_404(isolated_knowledge):
    client = TestClient(api_main.app)
    response = client.get("/knowledge/candidates/by-operator-file/MISSING")
    assert response.status_code == 404


def test_lookup_finds_most_recent_when_duplicate(isolated_knowledge):
    isolated_knowledge.create_approved_operator_upload(
        candidate_text="old",
        published_text="old",
        operator_username="@op",
        is_confidential=False,
        source_file_name="x",
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
        operator_short_id="DUP",
    )
    newer = isolated_knowledge.create_approved_operator_upload(
        candidate_text="new",
        published_text="new",
        operator_username="@op",
        is_confidential=False,
        source_file_name="y",
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
        operator_short_id="DUP",
    )
    client = TestClient(api_main.app)
    response = client.get("/knowledge/candidates/by-operator-file/DUP")
    assert response.status_code == 200
    assert response.json()["candidate_id"] == newer.id
