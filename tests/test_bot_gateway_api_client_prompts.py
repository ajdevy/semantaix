"""ApiClient prompt management methods used by /prompt_* bot commands."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.bot_gateway.app.api_client import ApiClient, ApiError


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://api")
            response = httpx.Response(
                self.status_code, request=request, json=self._body
            )
            raise httpx.HTTPStatusError(
                "err", request=request, response=response
            )

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    routes: dict[tuple[str, str], _FakeResponse] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> _FakeResponse:
        path = url.split(":8000", 1)[-1] if ":8000" in url else url
        _FakeClient.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json,
                "headers": headers,
            }
        )
        return _FakeClient.routes.get(
            (method.upper(), path), _FakeResponse(404, {"detail": "not_found"})
        )


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.routes = {}
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return _FakeClient


def _client() -> ApiClient:
    return ApiClient(
        base_url="http://api:8000",
        timeout_seconds=10,
        internal_token="ignored",
    )


@pytest.mark.asyncio
async def test_list_project_prompts(fake_client):
    fake_client.routes[("GET", "/projects/default/prompts")] = _FakeResponse(
        200, {"items": []}
    )
    body = await _client().list_project_prompts(
        project_slug="default",
        requester_username="@alice",
        internal_token="tok",
    )
    assert body == {"items": []}
    call = fake_client.calls[0]
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["params"] == {"as_user": "@alice"}


@pytest.mark.asyncio
async def test_get_project_prompt(fake_client):
    fake_client.routes[
        ("GET", "/projects/default/prompts/verifier_system")
    ] = _FakeResponse(200, {"value": "x"})
    body = await _client().get_project_prompt(
        project_slug="default",
        prompt_name="verifier_system",
        requester_username="@alice",
        internal_token="tok",
    )
    assert body == {"value": "x"}


@pytest.mark.asyncio
async def test_restore_project_prompt(fake_client):
    fake_client.routes[
        ("POST", "/projects/default/prompts/verifier_system/restore")
    ] = _FakeResponse(200, {"version": 5})
    body = await _client().restore_project_prompt(
        project_slug="default",
        prompt_name="verifier_system",
        version=2,
        requester_username="@alice",
        internal_token="tok",
    )
    assert body == {"version": 5}
    assert fake_client.calls[0]["json"] == {"version": 2}


@pytest.mark.asyncio
async def test_arm_prompt_pending_edit(fake_client):
    fake_client.routes[
        ("POST", "/projects/default/prompts/verifier_system/pending")
    ] = _FakeResponse(200, {"ok": True})
    body = await _client().arm_prompt_pending_edit(
        project_slug="default",
        prompt_name="verifier_system",
        requester_username="@alice",
        internal_token="tok",
    )
    assert body == {"ok": True}


@pytest.mark.asyncio
async def test_peek_pending_prompt_edit_returns_body(fake_client):
    fake_client.routes[("GET", "/pending-prompt-edits")] = _FakeResponse(
        200, {"prompt_name": "verifier_system"}
    )
    body = await _client().peek_pending_prompt_edit(
        requester_username="@alice", internal_token="tok"
    )
    assert body == {"prompt_name": "verifier_system"}


@pytest.mark.asyncio
async def test_peek_pending_prompt_edit_returns_none_on_404(fake_client):
    fake_client.routes[("GET", "/pending-prompt-edits")] = _FakeResponse(
        404, {"detail": "no_pending_edit"}
    )
    body = await _client().peek_pending_prompt_edit(
        requester_username="@alice", internal_token="tok"
    )
    assert body is None


@pytest.mark.asyncio
async def test_cancel_pending_prompt_edit(fake_client):
    fake_client.routes[("DELETE", "/pending-prompt-edits")] = _FakeResponse(
        200, {"deleted": True}
    )
    body = await _client().cancel_pending_prompt_edit(
        requester_username="@alice", internal_token="tok"
    )
    assert body == {"deleted": True}


@pytest.mark.asyncio
async def test_consume_pending_prompt_edit(fake_client):
    fake_client.routes[
        ("POST", "/pending-prompt-edits/consume")
    ] = _FakeResponse(200, {"version": 4})
    body = await _client().consume_pending_prompt_edit(
        value="new value",
        requester_username="@alice",
        internal_token="tok",
    )
    assert body == {"version": 4}
    assert fake_client.calls[0]["json"] == {"value": "new value"}


@pytest.mark.asyncio
async def test_consume_pending_prompt_edit_raises_on_422(fake_client):
    fake_client.routes[
        ("POST", "/pending-prompt-edits/consume")
    ] = _FakeResponse(
        422, {"detail": "grounding_system: must contain {name}"}
    )
    with pytest.raises(ApiError) as info:
        await _client().consume_pending_prompt_edit(
            value="bad",
            requester_username="@alice",
            internal_token="tok",
        )
    assert info.value.detail == "grounding_system: must contain {name}"
