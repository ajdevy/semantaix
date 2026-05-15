"""Epic 10 story 10.05: admin NL dialog end-to-end via api ASGI transport."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from services.bot_gateway.app.admin_nl_dialog import handle_admin_nl_dialog
from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

pytestmark = [pytest.mark.e2e, pytest.mark.epic("10")]


def _msg(text: str) -> NormalizedTelegramMessage:
    return NormalizedTelegramMessage(
        update_id=1,
        source_message_id=2,
        chat_id=10,
        user_id=99,
        username="@admin",
        text=text,
    )


@pytest.mark.story("10-05")
@pytest.mark.asyncio
async def test_nl_create_project_round_trip(tmp_path, monkeypatch):
    from services.api.app import main as api_main
    from services.api.app.admin_auth import AdminAuthRepository
    from services.api.app.admin_nl_ops import AdminNlOpsRepository
    from services.api.app.knowledge_moderation import (
        KnowledgeModerationRepository,
    )
    from services.api.app.operators import OperatorRepository
    from services.api.app.projects import ProjectRepository
    from services.api.app.rag import RagRepository

    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    operators = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    admin_auth = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    nl_ops = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    knowledge = KnowledgeModerationRepository(str(tmp_path / "k.sqlite3"))
    rag = RagRepository(str(tmp_path / "rag.sqlite3"))
    projects.ensure_default_project()
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "operator_repository", operators)
    monkeypatch.setattr(api_main, "admin_auth_repository", admin_auth)
    monkeypatch.setattr(api_main, "admin_nl_ops_repository", nl_ops)
    monkeypatch.setattr(api_main, "knowledge_moderation_repository", knowledge)
    monkeypatch.setattr(api_main, "rag_repository", rag)
    monkeypatch.setattr(api_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "secret")

    transport = httpx.ASGITransport(app=api_main.app)

    class StubAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("timeout", None)
            super().__init__(transport=transport, base_url="http://api", timeout=5)

    monkeypatch.setattr(httpx, "AsyncClient", StubAsyncClient)

    client = ApiClient(base_url="http://api", internal_token="secret")
    send_dm = AsyncMock()

    # NL propose
    await handle_admin_nl_dialog(
        normalized=_msg("создай проект billing Биллинг команда"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert "Создать проект" in send_dm.await_args.args[1]

    # Admin replies "да" — confirm and dispatch.
    send_dm.reset_mock()
    await handle_admin_nl_dialog(
        normalized=_msg("да"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert "Операция применена" in send_dm.await_args.args[1]
    assert projects.get_by_slug("billing") is not None


@pytest.mark.story("10-05")
@pytest.mark.asyncio
async def test_nl_cancel_round_trip(tmp_path, monkeypatch):
    from services.api.app import main as api_main
    from services.api.app.admin_nl_ops import AdminNlOpsRepository
    from services.api.app.projects import ProjectRepository

    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    nl_ops = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    projects.ensure_default_project()
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "admin_nl_ops_repository", nl_ops)
    monkeypatch.setattr(api_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "secret")

    transport = httpx.ASGITransport(app=api_main.app)

    class StubAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("timeout", None)
            super().__init__(transport=transport, base_url="http://api", timeout=5)

    monkeypatch.setattr(httpx, "AsyncClient", StubAsyncClient)

    client = ApiClient(base_url="http://api", internal_token="secret")
    send_dm = AsyncMock()

    await handle_admin_nl_dialog(
        normalized=_msg("создай проект x X"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    await handle_admin_nl_dialog(
        normalized=_msg("нет"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert projects.get_by_slug("x") is None
