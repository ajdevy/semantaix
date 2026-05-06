from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import hitl_ticket_repository, incident_repository, openrouter_client


def test_suggest_returns_503_without_openrouter_key():
    openrouter_client.suggest = AsyncMock(
        side_effect=RuntimeError("OPENROUTER_API_KEY is not configured")
    )
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Hello"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_suggest_returns_suggestion_payload_on_success(monkeypatch):
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


def test_suggest_returns_502_on_provider_failure(monkeypatch):
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
