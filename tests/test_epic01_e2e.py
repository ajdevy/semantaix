import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.api.app.main import app as api_app
from services.api.app.main import openrouter_client
from services.bot_gateway.app.main import app as bot_app

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "telegram"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.e2e
@pytest.mark.epic("01")
@pytest.mark.story("01-04")
def test_epic01_e2e_webhook_persist_suggest(monkeypatch, tmp_path):
    db_path = tmp_path / "epic01_e2e.sqlite3"
    monkeypatch.setenv("PERSISTENCE_DB_PATH", str(db_path))
    get_settings.cache_clear()

    monkeypatch.setattr(
        openrouter_client,
        "suggest",
        AsyncMock(return_value="Thanks for reaching out. Here is a suggested reply."),
    )

    bot_client = TestClient(bot_app)
    webhook_response = bot_client.post(
        "/telegram/webhook",
        json=load_fixture("update_message_text_basic.json"),
    )
    assert webhook_response.status_code == 200
    assert webhook_response.json()["status"] == "accepted"

    with sqlite3.connect(db_path) as connection:
        conversation_count = connection.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]
        message_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        message_text = connection.execute("SELECT text FROM messages").fetchone()[0]

    assert conversation_count == 1
    assert message_count == 1
    assert message_text == "Hello, bot!"

    api_client = TestClient(api_app)
    suggest_response = api_client.post(
        "/suggest",
        json={"text": "User asked about billing. Suggest a response."},
    )
    assert suggest_response.status_code == 200
    data = suggest_response.json()
    assert data["response_mode"] == "suggestion_only"
    assert data["is_suggestion_only"] is True
    assert data["guardrails_applied"] is True
    assert data["suggestion"].startswith("[Suggestion mode]")

    get_settings.cache_clear()
