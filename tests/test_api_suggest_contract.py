from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import openrouter_client


def test_suggest_returns_503_without_openrouter_key():
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Hello"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_suggest_returns_suggestion_payload_on_success(monkeypatch):
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use reset flow."))
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "How can I reset my password?"})
    assert response.status_code == 200
    assert response.json() == {
        "suggestion": "[Suggestion mode] Use reset flow.",
        "is_suggestion_only": True,
        "response_mode": "suggestion_only",
        "guardrails_applied": False,
    }


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
