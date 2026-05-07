"""Epic 02: incident ingest, dedup, lifecycle transitions, and Telegram debounce."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    settings,
    telegram_notifier,
)

pytestmark = [pytest.mark.e2e, pytest.mark.epic("02")]


@pytest.mark.story("02-02")
def test_epic02_full_lifecycle_emit_read_ack_resolve_timeline(tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    telegram_notifier.bot_token = "replace-me"
    telegram_notifier.alert_chat_id = None
    client = TestClient(api_app)

    created = client.post(
        "/incidents/events",
        json={
            "fingerprint": "vectordb_down",
            "severity": "critical",
            "summary": "Vector DB unavailable",
        },
    ).json()
    incident_id = created["id"]

    listing = client.get("/incidents").json()
    assert len(listing["items"]) == 1
    assert listing["items"][0]["id"] == incident_id

    assert client.post(f"/incidents/{incident_id}/read").status_code == 200
    assert client.post(f"/incidents/{incident_id}/ack").status_code == 200
    resolved = client.post(f"/incidents/{incident_id}/resolve").json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None

    timeline = client.get(f"/incidents/{incident_id}/timeline").json()
    event_types = [event["event_type"] for event in timeline["events"]]
    assert event_types == [
        "created",
        "telegram_notify",
        "read",
        "acknowledged",
        "resolved",
    ]


@pytest.mark.story("02-01")
def test_epic02_dedup_within_window_collapses(tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    telegram_notifier.bot_token = "replace-me"
    telegram_notifier.alert_chat_id = None
    client = TestClient(api_app)

    payload = {
        "fingerprint": "queue_dlq_growth",
        "severity": "critical",
        "summary": "DLQ spike",
    }
    first = client.post("/incidents/events", json=payload).json()
    second = client.post("/incidents/events", json=payload).json()
    assert first["id"] == second["id"]
    assert second["occurrence_count"] == 2

    listing = client.get("/incidents/queue_dlq_growth").json()
    assert len(listing["items"]) == 1

    timeline = client.get(f"/incidents/{first['id']}/timeline").json()
    types = [event["event_type"] for event in timeline["events"]]
    assert types.count("created") == 1
    assert "deduplicated" in types


@pytest.mark.story("02-01")
def test_epic02_dedup_outside_window_creates_new_and_auto_resolves_prior(tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 0
    telegram_notifier.bot_token = "replace-me"
    telegram_notifier.alert_chat_id = None
    client = TestClient(api_app)

    payload = {
        "fingerprint": "db_down",
        "severity": "critical",
        "summary": "DB outage",
    }
    first = client.post("/incidents/events", json=payload).json()
    second = client.post("/incidents/events", json=payload).json()
    assert first["id"] != second["id"]

    listing = client.get("/incidents/db_down").json()
    statuses = {item["id"]: item["status"] for item in listing["items"]}
    assert statuses[first["id"]] == "resolved"
    assert statuses[second["id"]] == "open"

    prior_timeline = client.get(f"/incidents/{first['id']}/timeline").json()
    types = [event["event_type"] for event in prior_timeline["events"]]
    assert "auto_resolved" in types


@pytest.mark.story("02-03")
def test_epic02_critical_telegram_debounce(tmp_path, monkeypatch):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    settings.telegram_alert_debounce_seconds = 300
    mock_notify = AsyncMock(return_value=(True, "sent"))
    monkeypatch.setattr(telegram_notifier, "notify_if_critical", mock_notify)
    client = TestClient(api_app)

    payload = {
        "fingerprint": "provider5xx_spike",
        "severity": "critical",
        "summary": "Provider 5xx burst",
    }
    first = client.post("/incidents/events", json=payload).json()
    assert first["telegram_delivery_status"] == "sent"
    assert mock_notify.await_count == 1

    second = client.post("/incidents/events", json=payload).json()
    assert second["telegram_delivery_status"] == "debounced"
    assert mock_notify.await_count == 1

    settings.telegram_alert_debounce_seconds = 0
    third = client.post("/incidents/events", json=payload).json()
    assert third["telegram_delivery_status"] == "sent"
    assert mock_notify.await_count == 2


@pytest.mark.story("02-03")
def test_epic02_warning_does_not_send_telegram(tmp_path, monkeypatch):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    mock_notify = AsyncMock(return_value=(True, "sent"))
    monkeypatch.setattr(telegram_notifier, "notify_if_critical", mock_notify)
    client = TestClient(api_app)

    response = client.post(
        "/incidents/events",
        json={
            "fingerprint": "provider5xx_spike",
            "severity": "warning",
            "summary": "minor blip",
        },
    ).json()
    assert response["telegram_delivery_status"] == "not_critical"
    assert mock_notify.await_count == 0
