from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import (
    hitl_ticket_repository,
    incident_repository,
    openrouter_client,
    rag_repository,
)


def test_suggest_returns_503_without_openrouter_key():
    openrouter_client.suggest = AsyncMock(
        side_effect=RuntimeError("OPENROUTER_API_KEY is not configured")
    )
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Hello"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


@pytest.mark.e2e
@pytest.mark.epic("01")
@pytest.mark.story("01-03")
def test_suggest_returns_suggestion_payload_on_success(monkeypatch, tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "How can I reset my password?"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["suggestion"] == "[Suggestion mode] Use reset flow."
    assert payload["is_suggestion_only"] is True
    assert payload["response_mode"] == "suggestion_only"
    assert payload["guardrails_applied"] is True
    assert payload["guardrail_decision"]["valid"] is True
    assert payload["delivery_blocked"] is False
    assert payload["retrieval"] == []


def test_suggest_returns_502_on_provider_failure(monkeypatch, tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(side_effect=Exception("provider timeout")),
    )
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Help me"})
    assert response.status_code == 502
    assert "OpenRouter call failed" in response.json()["detail"]


def test_suggest_blocks_invalid_candidate_and_emits_incident(monkeypatch, tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="I don't know."))

    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Please answer this hard question."})
    assert response.status_code == 200

    payload = response.json()
    assert payload["suggestion"] is None
    assert payload["response_mode"] == "blocked_invalid"
    assert payload["guardrail_decision"]["valid"] is False
    assert "low_confidence" in payload["guardrail_decision"]["reasons"]
    assert payload["delivery_blocked"] is True

    incidents = client.get("/incidents/guardrail_invalid_suggestion").json()["items"]
    assert len(incidents) == 1


def test_suggest_passes_retrieval_context_to_llm(monkeypatch, tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    rag_repository.ingest(source_id="kb", text="reset password using email token")
    captured_context: list[dict[str, str]] = []

    async def _fake_suggest(user_text: str, context=None):
        assert user_text == "reset password help"
        assert context is not None
        captured_context.extend(context)
        return "Use reset flow."

    monkeypatch.setattr(openrouter_client, "suggest", _fake_suggest)
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "reset password help"})
    assert response.status_code == 200
    assert len(captured_context) == 1
    assert "Relevant knowledge" in captured_context[0]["content"]
    assert response.json()["retrieval"][0]["source_id"] == "kb"
