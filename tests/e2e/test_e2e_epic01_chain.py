"""Epic 01: Telegram webhook -> persistence -> /suggest with retrieval, plus failure paths."""

import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import (
    incident_repository,
    openrouter_client,
    rag_repository,
)
from services.api.app.main import settings as api_settings
from services.bot_gateway.app.main import app as bot_app
from tests.e2e.db_seed import load_telegram_fixture

pytestmark = [pytest.mark.e2e, pytest.mark.epic("01")]


@pytest.mark.story("01-04")
def test_epic01_e2e_webhook_persist_then_suggest_with_retrieval(monkeypatch, tmp_path):
    persistence_path = tmp_path / "persistence.sqlite3"
    # Patch in place on the cached settings instance so the bot_gateway's lazy
    # get_settings() call inside the webhook handler picks up the override
    # without disturbing the lru_cache (which would mismatch module-level
    # `settings` references in api/main.py and break later contract tests).
    monkeypatch.setattr(api_settings, "persistence_db_path", str(persistence_path))

    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(return_value="Substantial answer drawing on retrieved billing context."),
    )

    bot_client = TestClient(bot_app)
    webhook = bot_client.post(
        "/telegram/webhook",
        json=load_telegram_fixture("update_message_text_basic.json"),
    )
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "accepted"

    with sqlite3.connect(persistence_path) as connection:
        conversations = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert conversations == 1
    assert messages == 1

    api_client = TestClient(api_app)
    ingest = api_client.post(
        "/rag/ingest",
        json={
            "source_id": "faq-billing",
            "text": "Invoices generate on day one each month for active subscriptions.",
        },
    )
    assert ingest.status_code == 200

    suggest = api_client.post(
        "/suggest",
        json={"text": "When are invoices generated each month?"},
    )
    assert suggest.status_code == 200
    body = suggest.json()
    assert body["response_mode"] == "suggestion_only"
    assert body["delivery_blocked"] is False
    sources = [item["source_id"] for item in body["retrieval"]]
    assert "faq-billing" in sources


@pytest.mark.story("01-04")
def test_epic01_e2e_suggest_returns_503_when_openrouter_key_missing(monkeypatch, tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(side_effect=RuntimeError("OPENROUTER_API_KEY is not configured")),
    )

    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Any question"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


@pytest.mark.story("01-04")
def test_epic01_e2e_suggest_returns_502_on_provider_failure(monkeypatch, tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(side_effect=Exception("provider timeout")),
    )

    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Any question"})
    assert response.status_code == 502
    assert "provider timeout" in response.json()["detail"]
