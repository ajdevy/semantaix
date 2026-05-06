from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import incident_repository


def test_incident_event_endpoint_deduplicates_within_window(tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    client = TestClient(api_app)

    first = client.post(
        "/incidents/events",
        json={
            "fingerprint": "queue_dlq_growth",
            "severity": "critical",
            "summary": "DLQ growth spike",
        },
    )
    second = client.post(
        "/incidents/events",
        json={
            "fingerprint": "queue_dlq_growth",
            "severity": "critical",
            "summary": "DLQ growth spike",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["occurrence_count"] == 2


def test_get_incidents_by_fingerprint_returns_lifecycle_items(tmp_path):
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    incident_repository.dedup_window_seconds = 300
    client = TestClient(api_app)

    client.post(
        "/incidents/events",
        json={
            "fingerprint": "hitl_delivery_failures",
            "severity": "warning",
            "summary": "HITL notify failed",
        },
    )

    response = client.get("/incidents/hitl_delivery_failures")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fingerprint"] == "hitl_delivery_failures"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["status"] == "open"
    assert payload["items"][0]["severity"] == "warning"
