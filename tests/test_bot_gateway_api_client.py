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


@pytest.mark.asyncio
async def test_submit_operator_upload_posts_to_correct_endpoint(monkeypatch):
    http = _http_mock(monkeypatch, response_json={"candidate_id": 7})
    client = ApiClient(base_url="http://api:8000")
    result = await client.submit_operator_upload(
        operator_username="@op",
        source_file_type="pdf",
        source_file_name="schedule.pdf",
        stored_binary_path="/data/x.pdf",
        is_confidential=True,
        inline_text=None,
        timeout_seconds=42,
    )
    assert result == {"candidate_id": 7}
    args = http.post.await_args
    assert args.args[0] == "http://api:8000/knowledge/operator_upload"
    assert args.kwargs["json"] == {
        "operator_username": "@op",
        "source_file_type": "pdf",
        "source_file_name": "schedule.pdf",
        "stored_binary_path": "/data/x.pdf",
        "is_confidential": True,
        "inline_text": None,
    }


@pytest.mark.asyncio
async def test_submit_operator_upload_uses_default_timeout(monkeypatch):
    http = _http_mock(monkeypatch, response_json={"candidate_id": 1})
    client = ApiClient(base_url="http://api:8000", timeout_seconds=5)
    await client.submit_operator_upload(
        operator_username="@op",
        source_file_type="inline_text",
        source_file_name=None,
        stored_binary_path=None,
        is_confidential=False,
        inline_text="hello",
    )
    assert http.post.await_count == 1
