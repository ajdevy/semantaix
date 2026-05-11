import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.bot_gateway.app.main import app as bot_app
from services.bot_gateway.app.main import hitl_ticket_repository
from tests.e2e.db_seed import load_telegram_fixture as load_fixture


@pytest.fixture
def persistence_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "bot_gateway_test.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


@pytest.mark.e2e
@pytest.mark.epic("01")
@pytest.mark.story("01-01")
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


@pytest.mark.e2e
@pytest.mark.epic("01")
@pytest.mark.story("01-02")
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


@pytest.mark.e2e
@pytest.mark.epic("04")
@pytest.mark.story("04-runtime-config")
def test_admin_can_configure_hitl_contact_via_command(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    client = TestClient(bot_app)
    payload = {
        "update_id": 3001,
        "message": {
            "message_id": 700,
            "from": {"id": 1, "username": "ajdevy"},
            "chat": {"id": 1, "type": "private"},
            "text": "/hitl_config @flexsentlabs 650934815",
        },
    }
    response = client.post("/telegram/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "configured"
    assert data["hitl_primary_operator_username"] == "@flexsentlabs"
    assert data["telegram_alert_chat_id"] == "650934815"
    assert data["hitl_primary_operator_chat_id"] == "650934815"
    assert (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        == "@flexsentlabs"
    )
    assert (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_chat_id")
        == "650934815"
    )


def test_non_admin_cannot_configure_hitl_contact_via_command(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    client = TestClient(bot_app)
    payload = {
        "update_id": 3002,
        "message": {
            "message_id": 701,
            "from": {"id": 2, "username": "randomuser"},
            "chat": {"id": 2, "type": "private"},
            "text": "/hitl_config @x 123",
        },
    }
    response = client.post("/telegram/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "unauthorized_hitl_config"


def test_admin_hitl_config_rejects_bad_formats(tmp_path):
    hitl_ticket_repository.db_path = str(tmp_path / "hitl.sqlite3")
    client = TestClient(bot_app)

    invalid_format = {
        "update_id": 3003,
        "message": {
            "message_id": 702,
            "from": {"id": 1, "username": "ajdevy"},
            "chat": {"id": 1, "type": "private"},
            "text": "/hitl_config onlytwoargs",
        },
    }
    invalid_operator = {
        "update_id": 3004,
        "message": {
            "message_id": 703,
            "from": {"id": 1, "username": "ajdevy"},
            "chat": {"id": 1, "type": "private"},
            "text": "/hitl_config flexsentlabs 650934815",
        },
    }
    invalid_chat = {
        "update_id": 3005,
        "message": {
            "message_id": 704,
            "from": {"id": 1, "username": "ajdevy"},
            "chat": {"id": 1, "type": "private"},
            "text": "/hitl_config @flexsentlabs abc",
        },
    }

    response1 = client.post("/telegram/webhook", json=invalid_format)
    response2 = client.post("/telegram/webhook", json=invalid_operator)
    response3 = client.post("/telegram/webhook", json=invalid_chat)
    assert response1.json()["reason"] == "invalid_hitl_config_format"
    assert response2.json()["reason"] == "invalid_operator_username"
    assert response3.json()["reason"] == "invalid_chat_id"
