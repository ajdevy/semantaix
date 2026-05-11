from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from services.bot_gateway.app.api_client import ApiClient


def _http_mock(monkeypatch, *, response_json: dict):
    response = Mock()
    response.json.return_value = response_json
    response.raise_for_status = Mock()

    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=response)

    cm = AsyncMock()
    cm.__aenter__.return_value = http_client
    cm.__aexit__.return_value = None
    monkeypatch.setattr(
        "services.bot_gateway.app.api_client.httpx.AsyncClient",
        lambda timeout: cm,
    )
    return http_client


@pytest.mark.asyncio
async def test_forward_inbound_posts_to_correct_endpoint(monkeypatch):
    http = _http_mock(monkeypatch, response_json={"escalated": True})
    client = ApiClient(base_url="http://api:8000")
    result = await client.forward_inbound(
        text="hi",
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
    )
    assert result == {"escalated": True}
    args = http.post.await_args
    assert args.args[0] == "http://api:8000/conversations/inbound"
    assert args.kwargs["json"] == {
        "text": "hi",
        "chat_id": 1,
        "customer_username": "@c",
        "trace_id": "t-1",
    }


@pytest.mark.asyncio
async def test_deliver_operator_reply_posts_to_correct_endpoint(monkeypatch):
    http = _http_mock(monkeypatch, response_json={"delivered": True})
    client = ApiClient(base_url="http://api:8000/")
    result = await client.deliver_operator_reply(
        ticket_id=42, operator_username="@op", reply_text="hello"
    )
    assert result == {"delivered": True}
    args = http.post.await_args
    assert args.args[0] == "http://api:8000/hitl/tickets/42/reply"
    assert args.kwargs["json"] == {
        "operator_username": "@op",
        "reply_text": "hello",
    }
