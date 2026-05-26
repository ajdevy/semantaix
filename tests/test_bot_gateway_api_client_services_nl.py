"""ApiClient services NL-ops extensions tests (story 13.05)."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from services.bot_gateway.app.api_client import ApiClient, ApiError


class _Resp:
    """Fake response that also raises with an httpx.Response carrying the body
    so :func:`api_client._extract_detail` can read ``detail`` on errors."""

    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            real_response = httpx.Response(
                self.status_code, content=_json.dumps(self._body).encode()
            )
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("POST", "http://api"),
                response=real_response,
            )

    def json(self) -> Any:
        return self._body


class _Client:
    routes: dict[tuple[str, str], _Resp] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def get(self, url: str, *, headers=None, params=None) -> _Resp:
        return self._record("GET", url, None, headers, params)

    async def post(self, url: str, *, json=None, headers=None) -> _Resp:
        return self._record("POST", url, json, headers, None)

    @classmethod
    def _record(cls, method, url, body, headers, params) -> _Resp:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        path = parsed.path or url
        cls.calls.append(
            {
                "method": method,
                "url": url,
                "path": path,
                "json": body,
                "headers": headers,
                "params": params,
            }
        )
        return cls.routes.get((method, path), _Resp(404, {"detail": "no_route"}))


@pytest.fixture
def fake_client(monkeypatch):
    _Client.routes = {}
    _Client.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return _Client


@pytest.mark.asyncio
async def test_services_nl_propose_posts_with_bearer(fake_client):
    fake_client.routes[("POST", "/api/projects/1/services/nl-ops")] = _Resp(
        200,
        {
            "session_id": 7,
            "status": "pending_confirmation",
            "preview": "Создать услугу «маникюр».",
            "confirm_token": "tok-xyz",
            "expires_at": "2026-05-24T12:00:00+00:00",
        },
    )
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.services_nl_propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр",
        internal_token="bear",
    )
    assert body["session_id"] == 7
    call = fake_client.calls[0]
    assert call["headers"] == {"Authorization": "Bearer bear"}
    assert call["json"] == {
        "originating_operator": "@op",
        "text": "добавь услугу маникюр",
    }


@pytest.mark.asyncio
async def test_services_nl_propose_raises_api_error_with_detail(fake_client):
    fake_client.routes[("POST", "/api/projects/1/services/nl-ops")] = _Resp(
        400, {"detail": "invalid_project"}
    )

    # Patch _Resp.json to support ApiError detail extraction.
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    with pytest.raises(ApiError):
        await client.services_nl_propose(
            project_id=1,
            originating_operator="@op",
            text="x",
            internal_token="bear",
        )


@pytest.mark.asyncio
async def test_services_nl_confirm_posts_body(fake_client):
    fake_client.routes[
        ("POST", "/api/projects/1/services/nl-ops/7/confirm")
    ] = _Resp(200, {"status": "confirmed", "applied_op_type": "service_add"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.services_nl_confirm(
        project_id=1,
        session_id=7,
        presenter_operator="@op",
        confirm_token="tok",
        internal_token="bear",
    )
    assert body["applied_op_type"] == "service_add"
    call = fake_client.calls[0]
    assert call["json"] == {
        "presenter_operator": "@op",
        "confirm_token": "tok",
        "actor_role": "operator",
    }
    assert call["headers"] == {"Authorization": "Bearer bear"}


@pytest.mark.asyncio
async def test_services_nl_confirm_raises_with_detail(fake_client):
    fake_client.routes[
        ("POST", "/api/projects/1/services/nl-ops/7/confirm")
    ] = _Resp(401, {"detail": "invalid_confirm_token"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    with pytest.raises(ApiError) as ei:
        await client.services_nl_confirm(
            project_id=1,
            session_id=7,
            presenter_operator="@op",
            confirm_token="tok",
            internal_token="bear",
        )
    assert ei.value.detail == "invalid_confirm_token"


@pytest.mark.asyncio
async def test_services_nl_cancel_posts_presenter(fake_client):
    fake_client.routes[
        ("POST", "/api/projects/1/services/nl-ops/9/cancel")
    ] = _Resp(200, {"status": "cancelled"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.services_nl_cancel(
        project_id=1,
        session_id=9,
        presenter_operator="@op",
        internal_token="bear",
    )
    assert body == {"status": "cancelled"}
    call = fake_client.calls[0]
    assert call["json"] == {"presenter_operator": "@op"}


@pytest.mark.asyncio
async def test_services_nl_cancel_raises_api_error(fake_client):
    fake_client.routes[
        ("POST", "/api/projects/1/services/nl-ops/9/cancel")
    ] = _Resp(403, {"detail": "not_session_owner"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    with pytest.raises(ApiError) as ei:
        await client.services_nl_cancel(
            project_id=1,
            session_id=9,
            presenter_operator="@op",
            internal_token="bear",
        )
    assert ei.value.detail == "not_session_owner"


@pytest.mark.asyncio
async def test_services_nl_latest_pending_returns_body(fake_client):
    fake_client.routes[
        ("GET", "/api/projects/1/services/nl-ops/latest-pending")
    ] = _Resp(200, {"session_id": 7, "preview": "X", "status": "pending_confirmation"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.services_nl_latest_pending(
        project_id=1, operator="@op", internal_token="bear"
    )
    assert body == {"session_id": 7, "preview": "X", "status": "pending_confirmation"}
    call = fake_client.calls[0]
    assert call["params"] == {"operator": "@op"}
    assert call["headers"] == {"Authorization": "Bearer bear"}


@pytest.mark.asyncio
async def test_services_nl_latest_pending_returns_none_on_404(fake_client):
    fake_client.routes[
        ("GET", "/api/projects/1/services/nl-ops/latest-pending")
    ] = _Resp(404, {"detail": "no_pending"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.services_nl_latest_pending(
        project_id=1, operator="@op", internal_token="bear"
    )
    assert body is None


@pytest.mark.asyncio
async def test_services_nl_latest_pending_raises_on_5xx(fake_client):
    fake_client.routes[
        ("GET", "/api/projects/1/services/nl-ops/latest-pending")
    ] = _Resp(500, {"detail": "boom"})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    with pytest.raises(ApiError):
        await client.services_nl_latest_pending(
            project_id=1, operator="@op", internal_token="bear"
        )
