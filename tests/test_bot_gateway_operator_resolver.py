"""Tests for the bot's multi-operator resolver."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from services.bot_gateway.app.operator_resolver import (
    ResolvedOperator,
    resolve_operator_for_sender,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://api")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class FakeApi:
    def __init__(self):
        self.find_operator_by_username = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_empty_username_returns_none():
    api = FakeApi()
    result = await resolve_operator_for_sender(
        username=None,
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is None
    api.find_operator_by_username.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_active_operator_resolves():
    api = FakeApi()
    api.find_operator_by_username.return_value = {
        "username": "@op-b",
        "chat_id": 200,
        "project_id": 2,
        "is_active": True,
    }
    result = await resolve_operator_for_sender(
        username="@op-b",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result == ResolvedOperator(
        username="@op-b",
        chat_id=200,
        project_id=2,
        is_active=True,
        source="registry",
    )


@pytest.mark.asyncio
async def test_inactive_operator_returns_none():
    api = FakeApi()
    api.find_operator_by_username.return_value = {
        "username": "@op-b",
        "chat_id": 200,
        "project_id": 2,
        "is_active": False,
    }
    result = await resolve_operator_for_sender(
        username="@op-b",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is None


@pytest.mark.asyncio
async def test_unknown_username_with_no_fallback_returns_none():
    api = FakeApi()
    result = await resolve_operator_for_sender(
        username="@ghost",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is None


@pytest.mark.asyncio
async def test_primary_fallback_on_api_5xx():
    api = FakeApi()
    api.find_operator_by_username.side_effect = _http_error(500)
    result = await resolve_operator_for_sender(
        username="@primary",
        api_client=api,
        primary_operator_username="@primary",
        primary_operator_chat_id=99,
    )
    assert result is not None
    assert result.source == "primary_fallback"
    assert result.chat_id == 99


@pytest.mark.asyncio
async def test_primary_fallback_on_network_error():
    api = FakeApi()
    api.find_operator_by_username.side_effect = httpx.ConnectError("boom")
    result = await resolve_operator_for_sender(
        username="@primary",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is not None
    assert result.source == "primary_fallback"


@pytest.mark.asyncio
async def test_no_fallback_for_non_primary_on_failure():
    api = FakeApi()
    api.find_operator_by_username.side_effect = _http_error(500)
    result = await resolve_operator_for_sender(
        username="@stranger",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is None


@pytest.mark.asyncio
async def test_chat_id_none_handled():
    api = FakeApi()
    api.find_operator_by_username.return_value = {
        "username": "@op",
        "chat_id": None,
        "project_id": 1,
        "is_active": True,
    }
    result = await resolve_operator_for_sender(
        username="@op",
        api_client=api,
        primary_operator_username="@primary",
    )
    assert result is not None
    assert result.chat_id is None
