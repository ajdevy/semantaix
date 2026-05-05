import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.bot_gateway.app.main import app as bot_app

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "telegram"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_webhook_accepts_text_message_and_returns_trace():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_message_text_basic.json"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert isinstance(data["trace_id"], str) and len(data["trace_id"]) > 0


def test_webhook_ignores_empty_text_message():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_message_text_empty.json"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_rejects_malformed_payload():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_malformed_missing_core.json"),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "missing_or_invalid_update_id"


def test_webhook_ignores_callback_query_update():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_callback_query_valid.json"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_edited_message_update():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_edited_message_valid.json"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_non_text_message():
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_non_text_message_photo.json"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_duplicate_update_fixture_is_idempotent_at_gateway_level():
    client = TestClient(bot_app)
    payload = load_fixture("update_duplicate_update_id.json")
    first = client.post("/telegram/webhook", json=payload)
    second = client.post("/telegram/webhook", json=payload)
    assert first.status_code == 200 and first.json()["status"] == "accepted"
    assert second.status_code == 200 and second.json()["status"] == "accepted"


def test_webhook_rejects_non_object_payload():
    client = TestClient(bot_app)
    response = client.post("/telegram/webhook", json=["bad", "payload"])
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_payload_type"
