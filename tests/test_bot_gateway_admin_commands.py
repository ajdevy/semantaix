"""Admin Telegram commands dispatcher tests for Epic 10 story 10.04."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from services.bot_gateway.app.admin_commands import (
    handle_admin_project_command,
)
from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage


def _message(text: str, *, username: str = "@admin") -> NormalizedTelegramMessage:
    return NormalizedTelegramMessage(
        update_id=1,
        source_message_id=2,
        chat_id=10,
        user_id=999,
        username=username,
        text=text,
    )


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://api")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class FakeApi:
    def __init__(self) -> None:
        self.list_projects = AsyncMock(return_value={"items": []})
        self.create_project = AsyncMock(return_value={"id": 9, "slug": "x"})
        self.list_operators = AsyncMock(return_value={"items": []})
        self.attach_operator = AsyncMock(return_value={"id": 5})
        self.detach_operator = AsyncMock(return_value={"id": 5})
        self.find_candidate_by_short_id = AsyncMock(
            return_value={"candidate_id": 11}
        )
        self.reassign_candidate = AsyncMock(return_value={"ok": True})


@pytest.fixture
def fake_api():
    return FakeApi()


@pytest.fixture
def send_dm():
    return AsyncMock()


@pytest.mark.asyncio
async def test_dispatcher_ignores_non_admin(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("/projects", username="@user"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result is None
    send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_ignores_missing_username(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("/projects", username=""),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result is None


@pytest.mark.asyncio
async def test_dispatcher_unknown_command_returns_none(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("hello"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result is None


@pytest.mark.asyncio
async def test_projects_list_empty(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("/projects"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "projects_list", "count": "0"}
    send_dm.assert_awaited_once()
    assert "пока нет" in send_dm.await_args.args[1].lower()


@pytest.mark.asyncio
async def test_projects_list_with_items(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [
            {
                "id": 1,
                "slug": "default",
                "name": "Default",
                "description": "",
            },
            {
                "id": 2,
                "slug": "billing",
                "name": "Биллинг",
                "description": "ops",
            },
        ]
    }
    result = await handle_admin_project_command(
        normalized=_message("/projects"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "projects_list", "count": "2"}
    body = send_dm.await_args.args[1]
    assert "billing" in body
    assert "ops" in body


@pytest.mark.asyncio
async def test_project_new_happy(fake_api, send_dm):
    fake_api.create_project.return_value = {"id": 99, "slug": "qa"}
    result = await handle_admin_project_command(
        normalized=_message("/project_new qa Quality assurance"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {
        "status": "ok",
        "route": "project_new",
        "project_id": "99",
    }
    fake_api.create_project.assert_awaited_once_with(
        slug="qa", name="Quality assurance"
    )


@pytest.mark.asyncio
async def test_project_new_conflict(fake_api, send_dm):
    fake_api.create_project.side_effect = _http_error(409)
    result = await handle_admin_project_command(
        normalized=_message("/project_new qa Quality"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "conflict"
    assert "уже существует" in send_dm.await_args.args[1]


@pytest.mark.asyncio
async def test_project_new_server_error(fake_api, send_dm):
    fake_api.create_project.side_effect = _http_error(500)
    result = await handle_admin_project_command(
        normalized=_message("/project_new qa Quality"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_operator_add_happy(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    result = await handle_admin_project_command(
        normalized=_message("/operator_add @op-b billing 12345"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "operator_add"}
    fake_api.attach_operator.assert_awaited_once_with(
        username="@op-b", project_id=7, chat_id=12345
    )


@pytest.mark.asyncio
async def test_operator_add_without_chat_id(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    await handle_admin_project_command(
        normalized=_message("/operator_add @op-b billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    fake_api.attach_operator.assert_awaited_once_with(
        username="@op-b", project_id=7, chat_id=None
    )


@pytest.mark.asyncio
async def test_operator_add_unknown_project(fake_api, send_dm):
    fake_api.list_projects.return_value = {"items": []}
    result = await handle_admin_project_command(
        normalized=_message("/operator_add @op-b ghost"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "project_missing"
    fake_api.attach_operator.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_add_conflict(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.attach_operator.side_effect = _http_error(409)
    result = await handle_admin_project_command(
        normalized=_message("/operator_add @op-b billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "conflict"


@pytest.mark.asyncio
async def test_operator_add_other_error(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.attach_operator.side_effect = _http_error(500)
    result = await handle_admin_project_command(
        normalized=_message("/operator_add @op-b billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_operator_remove_happy(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("/operator_remove @op-x"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "operator_remove"}


@pytest.mark.asyncio
async def test_operator_remove_missing(fake_api, send_dm):
    fake_api.detach_operator.side_effect = _http_error(404)
    result = await handle_admin_project_command(
        normalized=_message("/operator_remove @op-x"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "missing"


@pytest.mark.asyncio
async def test_operator_remove_other_error(fake_api, send_dm):
    fake_api.detach_operator.side_effect = _http_error(500)
    result = await handle_admin_project_command(
        normalized=_message("/operator_remove @op-x"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_operator_list_empty(fake_api, send_dm):
    result = await handle_admin_project_command(
        normalized=_message("/operator_list"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "operator_list", "count": "0"}


@pytest.mark.asyncio
async def test_operator_list_renders_items(fake_api, send_dm):
    fake_api.list_operators.return_value = {
        "items": [
            {
                "id": 1,
                "username": "@op-a",
                "chat_id": 99,
                "project_id": 1,
                "is_active": True,
            },
            {
                "id": 2,
                "username": "@op-b",
                "chat_id": None,
                "project_id": 2,
                "is_active": False,
            },
        ]
    }
    result = await handle_admin_project_command(
        normalized=_message("/operator_list"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["count"] == "2"
    body = send_dm.await_args.args[1]
    assert "@op-a" in body
    assert "@op-b" in body
    assert "inactive" in body


@pytest.mark.asyncio
async def test_file_assign_happy(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.find_candidate_by_short_id.return_value = {"candidate_id": 42}
    result = await handle_admin_project_command(
        normalized=_message("/file_assign #ABC123 billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result == {"status": "ok", "route": "file_assign"}
    fake_api.reassign_candidate.assert_awaited_once_with(
        candidate_id=42, project_id=7
    )


@pytest.mark.asyncio
async def test_file_assign_unknown_project(fake_api, send_dm):
    fake_api.list_projects.return_value = {"items": []}
    result = await handle_admin_project_command(
        normalized=_message("/file_assign #ABC123 ghost"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "project_missing"


@pytest.mark.asyncio
async def test_file_assign_candidate_not_found(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.find_candidate_by_short_id.side_effect = _http_error(404)
    result = await handle_admin_project_command(
        normalized=_message("/file_assign #ABC123 billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["decision"] == "candidate_missing"


@pytest.mark.asyncio
async def test_file_assign_lookup_error(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.find_candidate_by_short_id.side_effect = _http_error(500)
    result = await handle_admin_project_command(
        normalized=_message("/file_assign #ABC123 billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_file_assign_reassign_error(fake_api, send_dm):
    fake_api.list_projects.return_value = {
        "items": [{"id": 7, "slug": "billing"}]
    }
    fake_api.reassign_candidate.side_effect = _http_error(500)
    result = await handle_admin_project_command(
        normalized=_message("/file_assign #ABC123 billing"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_dispatcher_ignores_unknown_admin_slash(fake_api, send_dm):
    """Unknown admin command must NOT block the routing chain (returns None)."""
    result = await handle_admin_project_command(
        normalized=_message("/garbage_unknown_command"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
    )
    assert result is None
    send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_file_repository_argument_is_optional(fake_api, send_dm):
    from services.bot_gateway.app.operator_files import OperatorFileRepository

    # Just verify the dispatcher accepts the kwarg without using it yet.
    result = await handle_admin_project_command(
        normalized=_message("/projects"),
        api_client=fake_api,
        send_dm=send_dm,
        admin_username="@admin",
        operator_file_repository=OperatorFileRepository(":memory:"),
    )
    assert result is not None
