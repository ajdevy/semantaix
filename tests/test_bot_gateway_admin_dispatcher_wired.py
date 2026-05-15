"""Ensure the admin project command dispatcher is wired into the webhook chain."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.bot_gateway.app import main as bot_main
from services.bot_gateway.app.main import app as bot_app


class _StubHitlRepo:
    def get_runtime_config(self, key: str):
        return None

    def set_runtime_config(self, **_kwargs):
        pass


@pytest.fixture
def wired_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bot_main.settings, "persistence_db_path", str(tmp_path / "story.db")
    )
    monkeypatch.setattr(
        bot_main.settings, "hitl_ticket_db_path", str(tmp_path / "hitl.db")
    )
    monkeypatch.setattr(bot_main.settings, "telegram_bot_token", "TKN")
    monkeypatch.setattr(bot_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(bot_main, "hitl_ticket_repository", _StubHitlRepo())

    async def fake_send_dm(chat_id, text):
        return None

    monkeypatch.setattr(bot_main, "_send_dm", fake_send_dm)

    list_projects = AsyncMock(
        return_value={"items": [{"id": 1, "slug": "default", "name": "Default"}]}
    )
    monkeypatch.setattr(bot_main.api_client, "list_projects", list_projects)
    return tmp_path


def _admin_message(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 1},
            "from": {"id": 1, "username": "admin"},
            "text": text,
        },
    }


def test_webhook_routes_admin_project_command(wired_bot):
    client = TestClient(bot_app)
    response = client.post("/telegram/webhook", json=_admin_message("/projects"))
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "projects_list"
    assert "trace_id" in body
