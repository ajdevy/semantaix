"""ApiClient NL ops extensions tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.bot_gateway.app.api_client import ApiClient


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "http://api"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._body


class _Client:
    routes: dict[tuple[str, str], _Resp] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        return None

    async def get(self, url, *, headers=None):
        return self._record("GET", url, None, headers)

    async def post(self, url, *, json=None, headers=None):
        return self._record("POST", url, json, headers)

    @classmethod
    def _record(cls, method, url, body, headers):
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        path = parsed.path or url
        cls.calls.append(
            {"method": method, "url": url, "path": path, "json": body,
             "headers": headers}
        )
        return cls.routes.get((method, path), _Resp(404, {}))


@pytest.fixture
def fake_client(monkeypatch):
    _Client.routes = {}
    _Client.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return _Client


@pytest.mark.asyncio
async def test_admin_nl_ops_propose(fake_client):
    fake_client.routes[("POST", "/admin/nl-ops")] = _Resp(
        200, {"id": 7, "status": "pending_confirmation"}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.admin_nl_ops_propose(
        admin_username="@admin", utterance="x"
    )
    assert body["id"] == 7
    assert fake_client.calls[0]["json"] == {
        "admin_username": "@admin",
        "utterance": "x",
    }


@pytest.mark.asyncio
async def test_admin_nl_ops_confirm(fake_client):
    fake_client.routes[("POST", "/admin/nl-ops/7/confirm")] = _Resp(
        200, {"status": "confirmed"}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    await client.admin_nl_ops_confirm(session_id=7, confirm_token="tok")
    assert fake_client.calls[0]["json"] == {"confirm_token": "tok"}


@pytest.mark.asyncio
async def test_admin_nl_ops_cancel(fake_client):
    fake_client.routes[("POST", "/admin/nl-ops/7/cancel")] = _Resp(
        200, {"status": "cancelled"}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    await client.admin_nl_ops_cancel(session_id=7)
    assert fake_client.calls[0]["json"] == {}


@pytest.mark.asyncio
async def test_admin_nl_ops_latest_pending(fake_client):
    fake_client.routes[
        ("GET", "/admin/nl-ops/latest-pending")
    ] = _Resp(200, {"found": True, "id": 1})
    client = ApiClient(base_url="http://api:8000", internal_token="t")
    body = await client.admin_nl_ops_latest_pending(admin_username="@admin")
    assert body == {"found": True, "id": 1}
    assert "admin_username=@admin" in fake_client.calls[0]["url"]
