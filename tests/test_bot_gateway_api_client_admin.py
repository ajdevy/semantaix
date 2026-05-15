"""ApiClient extensions for Epic 10 admin commands."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.bot_gateway.app.api_client import ApiClient


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

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

    async def get(self, url, *, headers=None):
        return self._record("GET", url, json=None, headers=headers)

    async def post(self, url, *, json=None, headers=None):
        return self._record("POST", url, json=json, headers=headers)

    async def patch(self, url, *, json=None, headers=None):
        return self._record("PATCH", url, json=json, headers=headers)

    @classmethod
    def _record(
        cls,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> _FakeResponse:
        path = url.split(":8000", 1)[-1] if ":8000" in url else url
        cls.calls.append(
            {"method": method, "url": url, "path": path, "json": json,
             "headers": headers}
        )
        return cls.routes.get((method, path), _FakeResponse(404, {}))


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.routes = {}
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return _FakeClient


@pytest.mark.asyncio
async def test_list_projects_sends_internal_token(fake_client):
    fake_client.routes[("GET", "/projects")] = _FakeResponse(
        200, {"items": []}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    body = await client.list_projects()
    assert body == {"items": []}
    headers = fake_client.calls[0]["headers"]
    assert headers["X-Internal-Token"] == "secret"


@pytest.mark.asyncio
async def test_create_project(fake_client):
    fake_client.routes[("POST", "/projects")] = _FakeResponse(
        200, {"id": 1, "slug": "x"}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.create_project(slug="x", name="X", description="d")
    payload = fake_client.calls[0]["json"]
    assert payload == {"slug": "x", "name": "X", "description": "d"}


@pytest.mark.asyncio
async def test_list_operators_uses_get(fake_client):
    fake_client.routes[("GET", "/operators")] = _FakeResponse(
        200, {"items": []}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.list_operators()
    assert fake_client.calls[0]["method"] == "GET"


@pytest.mark.asyncio
async def test_attach_operator_includes_optional_fields(fake_client):
    fake_client.routes[("POST", "/operators")] = _FakeResponse(
        200, {"id": 1}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.attach_operator(
        username="@a",
        project_id=2,
        chat_id=99,
        display_name="A",
    )
    body = fake_client.calls[0]["json"]
    assert body == {
        "username": "@a",
        "project_id": 2,
        "chat_id": 99,
        "display_name": "A",
    }


@pytest.mark.asyncio
async def test_attach_operator_skips_unset_optionals(fake_client):
    fake_client.routes[("POST", "/operators")] = _FakeResponse(
        200, {"id": 1}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.attach_operator(username="@a", project_id=2)
    body = fake_client.calls[0]["json"]
    assert body == {"username": "@a", "project_id": 2}


@pytest.mark.asyncio
async def test_detach_operator_uses_patch(fake_client):
    fake_client.routes[("PATCH", "/operators/@a")] = _FakeResponse(
        200, {"id": 1}
    )
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.detach_operator(username="@a")
    assert fake_client.calls[0]["method"] == "PATCH"
    assert fake_client.calls[0]["json"] == {"is_active": False}


@pytest.mark.asyncio
async def test_find_candidate_by_short_id(fake_client):
    fake_client.routes[
        ("GET", "/knowledge/candidates/by-operator-file/ABC")
    ] = _FakeResponse(200, {"candidate_id": 7})
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    body = await client.find_candidate_by_short_id(short_id="ABC")
    assert body == {"candidate_id": 7}


@pytest.mark.asyncio
async def test_reassign_candidate(fake_client):
    fake_client.routes[
        ("POST", "/knowledge/candidates/9/reassign")
    ] = _FakeResponse(200, {"ok": True})
    client = ApiClient(base_url="http://api:8000", internal_token="secret")
    await client.reassign_candidate(candidate_id=9, project_id=2)
    assert fake_client.calls[0]["json"] == {"project_id": 2}


@pytest.mark.asyncio
async def test_submit_operator_upload_includes_short_id(fake_client):
    fake_client.routes[("POST", "/knowledge/operator_upload")] = _FakeResponse(
        200, {"candidate_id": 1}
    )
    client = ApiClient(base_url="http://api:8000")
    await client.submit_operator_upload(
        operator_username="@op",
        source_file_type="text",
        source_file_name="x.txt",
        stored_binary_path="/tmp/x",
        is_confidential=False,
        operator_short_id="ABC123",
    )
    body = fake_client.calls[0]["json"]
    assert body["operator_short_id"] == "ABC123"


@pytest.mark.asyncio
async def test_internal_token_empty_omits_header(fake_client):
    fake_client.routes[("GET", "/projects")] = _FakeResponse(200, {"items": []})
    client = ApiClient(base_url="http://api:8000", internal_token="")
    await client.list_projects()
    headers = fake_client.calls[0]["headers"]
    assert headers is None or "X-Internal-Token" not in headers
