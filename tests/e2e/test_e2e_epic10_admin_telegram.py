"""Epic 10 story 10.04: admin Telegram commands end-to-end."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.bot_gateway.app.admin_commands import (
    handle_admin_project_command,
)
from services.bot_gateway.app.api_client import ApiClient
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

pytestmark = [pytest.mark.e2e, pytest.mark.epic("10")]


def _message(text: str) -> NormalizedTelegramMessage:
    return NormalizedTelegramMessage(
        update_id=1,
        source_message_id=2,
        chat_id=10,
        user_id=99,
        username="@admin",
        text=text,
    )


@pytest.mark.story("10-04")
@pytest.mark.asyncio
async def test_admin_commands_dispatch_against_real_api(tmp_path, monkeypatch):
    from services.api.app import main as api_main
    from services.api.app.admin_auth import AdminAuthRepository
    from services.api.app.knowledge_moderation import (
        KnowledgeModerationRepository,
    )
    from services.api.app.operators import OperatorRepository
    from services.api.app.projects import ProjectRepository
    from services.api.app.rag import RagRepository

    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    operators = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    admin_auth = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    knowledge = KnowledgeModerationRepository(str(tmp_path / "k.sqlite3"))
    rag = RagRepository(str(tmp_path / "rag.sqlite3"))
    default = projects.ensure_default_project()
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "operator_repository", operators)
    monkeypatch.setattr(api_main, "admin_auth_repository", admin_auth)
    monkeypatch.setattr(api_main, "knowledge_moderation_repository", knowledge)
    monkeypatch.setattr(api_main, "rag_repository", rag)
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "secret")

    # Stand up an ASGI httpx transport so ApiClient hits api_main.app
    # directly without spawning a server.
    import httpx

    transport = httpx.ASGITransport(app=api_main.app)

    class StubAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("timeout", None)
            super().__init__(
                transport=transport, base_url="http://api", timeout=5
            )

    monkeypatch.setattr(httpx, "AsyncClient", StubAsyncClient)

    client = ApiClient(base_url="http://api", internal_token="secret")
    send_dm = AsyncMock()

    # /project_new
    await handle_admin_project_command(
        normalized=_message("/project_new billing Биллинг команда"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    project = projects.get_by_slug("billing")
    assert project is not None

    # /operator_add @op-b billing 12345
    await handle_admin_project_command(
        normalized=_message("/operator_add @op-b billing 12345"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    operator = operators.find_by_username("@op-b")
    assert operator is not None
    assert operator.project_id == project.id
    assert operator.chat_id == 12345

    # Upload a file with operator_short_id metadata.
    candidate = knowledge.create_approved_operator_upload(
        candidate_text="content",
        published_text="content",
        operator_username="@admin",
        is_confidential=False,
        source_file_name="doc.pdf",
        source_file_type="pdf",
        stored_binary_path=None,
        binary_sha256=None,
        operator_short_id="ABCD1234",
    )
    rag.ingest(source_id=f"knowledge_candidate:{candidate.id}", text="content")

    # /file_assign #ABCD1234 billing
    await handle_admin_project_command(
        normalized=_message("/file_assign #ABCD1234 billing"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    refreshed = knowledge.get(candidate.id)
    assert refreshed.project_id == project.id

    # /operator_remove @op-b → marks inactive but row persists.
    await handle_admin_project_command(
        normalized=_message("/operator_remove @op-b"),
        api_client=client,
        send_dm=send_dm,
        admin_username="@admin",
    )
    op_after = operators.find_by_username("@op-b")
    assert op_after is not None
    assert op_after.is_active is False
    assert default.id  # used for assertion shape (silences unused warning)
