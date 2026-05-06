import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.bot_gateway.app.main import app as bot_app

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "telegram"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def persistence_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "bot_gateway_test.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


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


def test_webhook_persists_message_rows(persistence_db):
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=load_fixture("update_message_text_basic.json"),
    )
    assert response.status_code == 200

    with sqlite3.connect(persistence_db) as connection:
        conversations = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert conversations == 1
    assert messages == 1


def test_duplicate_webhook_does_not_create_duplicate_message_row(persistence_db):
    client = TestClient(bot_app)
    payload = load_fixture("update_duplicate_update_id.json")
    first = client.post("/telegram/webhook", json=payload)
    second = client.post("/telegram/webhook", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200

    with sqlite3.connect(persistence_db) as connection:
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert messages == 1
