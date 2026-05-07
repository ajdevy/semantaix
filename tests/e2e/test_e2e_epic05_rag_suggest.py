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


def test_epic05_rag_ingest_dedup_returns_zero_chunks_second_call(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    payload = {
        "source_id": "faq-refund",
        "text": "Refunds are processed within five business days.",
    }
    first = client.post("/rag/ingest", json=payload).json()
    second = client.post("/rag/ingest", json=payload).json()
    assert first["inserted_chunks"] == 1
    assert second["inserted_chunks"] == 0

    items = client.post(
        "/rag/retrieve",
        json={"query": "refund processed days", "limit": 5},
    ).json()["items"]
    assert len([item for item in items if item["source_id"] == "faq-refund"]) == 1


def test_epic05_rag_retrieve_ranks_higher_overlap_source_first(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    client.post(
        "/rag/ingest",
        json={
            "source_id": "faq-passwords",
            "text": "Reset password via settings menu and confirm with email link.",
        },
    )
    client.post(
        "/rag/ingest",
        json={
            "source_id": "faq-billing",
            "text": "Billing cycle runs monthly with invoice and a reset on day one.",
        },
    )

    response = client.post(
        "/rag/retrieve",
        json={"query": "reset password settings email", "limit": 5},
    ).json()
    sources = [item["source_id"] for item in response["items"]]
    assert sources[0] == "faq-passwords"
    assert "faq-billing" in sources
    passwords_score = next(
        item["score"] for item in response["items"] if item["source_id"] == "faq-passwords"
    )
    billing_score = next(
        item["score"] for item in response["items"] if item["source_id"] == "faq-billing"
    )
    assert passwords_score > billing_score


def test_epic05_rag_ingest_failure_emits_incident(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")

    def _boom(**kwargs):
        raise ValueError("disk full")

    monkeypatch.setattr(rag_repository, "ingest", _boom)
    client = TestClient(api_app)

    response = client.post(
        "/rag/ingest",
        json={"source_id": "faq-x", "text": "anything"},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "rag_ingest_failed"

    incidents = client.get("/incidents/rag_ingest_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"
