"""Epic 05 + 03: RAG ingest drives context on /suggest (happy path)."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import incident_repository, openrouter_client, rag_repository

pytestmark = [pytest.mark.e2e, pytest.mark.epic("05"), pytest.mark.story("05-02")]


def test_epic05_rag_ingest_then_suggest_includes_retrieval(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    ingest = client.post(
        "/rag/ingest",
        json={
            "source_id": "faq-billing",
            "text": "Invoices generate on day one each month for the billing cycle.",
        },
    )
    assert ingest.status_code == 200

    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(
            return_value="Here is a safe and grounded summary of billing for the customer."
        ),
    )

    suggestion = client.post(
        "/suggest",
        json={"text": "When does invoicing happen in the billing cycle?"},
    )
    assert suggestion.status_code == 200
    payload = suggestion.json()
    assert payload["response_mode"] == "suggestion_only"
    assert payload["delivery_blocked"] is False
    retrieval = payload["retrieval"]
    assert isinstance(retrieval, list) and len(retrieval) >= 1
    assert any(item["source_id"] == "faq-billing" for item in retrieval)
    assert "billing" in retrieval[0]["chunk_text"].lower()
