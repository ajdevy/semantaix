"""Web UI admin pages for Epic 10 story 10.03.

Tests use a `FakeApiClient` that intercepts httpx.AsyncClient calls and
returns recorded responses, so the web_ui handlers exercise their real
forwarding logic without needing the api service running.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from services.web_ui.app import admin as admin_module
from services.web_ui.app.main import app as web_ui_app


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> dict:
        return self._body


class FakeApiClient:
    """Stand-in for httpx.AsyncClient that returns scripted responses."""

    routes: dict[tuple[str, str], FakeResponse] = {}
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
        headers: dict | None = None,
    ) -> FakeResponse:
        # Strip scheme + host so route stubs are keyed by path only.
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        path = parsed.path or url
        FakeApiClient.calls.append(
            {
                "method": method,
                "url": url,
                "path": path,
                "json": json,
                "headers": headers,
            }
        )
        key = (method.upper(), path)
        response = FakeApiClient.routes.get(key)
        if response is None:
            return FakeResponse(404, {"detail": f"route_not_stubbed:{path}"})
        return response


@pytest.fixture
def fake_api(monkeypatch):
    FakeApiClient.routes = {}
    FakeApiClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeApiClient)
    return FakeApiClient


@pytest.fixture
def authed_client(fake_api):
    """TestClient pre-loaded with a valid admin_session cookie + session-check stub."""
    fake_api.routes[("GET", "/admin/session/check")] = FakeResponse(
        200, {"admin_username": "@admin", "expires_at": "2099-12-31T00:00:00+00:00"}
    )
    client = TestClient(web_ui_app)
    client.cookies.set("admin_session", "valid-token")
    return client


def test_login_form_renders_admin_username(fake_api):
    client = TestClient(web_ui_app)
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "Admin login" in response.text
    assert "admin_username" in response.text


def test_login_post_requests_code_and_shows_code_form(fake_api):
    fake_api.routes[("POST", "/admin/login/request")] = FakeResponse(
        200, {"requested": True}
    )
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/login", data={"admin_username": "@admin"}
    )
    assert response.status_code == 200
    assert "Enter login code" in response.text
    assert "@admin" in response.text


def test_login_post_renders_failure(fake_api):
    fake_api.routes[("POST", "/admin/login/request")] = FakeResponse(
        403, {"detail": "not_admin"}
    )
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/login", data={"admin_username": "@stranger"}
    )
    assert response.status_code == 403
    assert "Failed to send code" in response.text


def test_verify_sets_cookie_on_success(fake_api):
    fake_api.routes[("POST", "/admin/login/verify")] = FakeResponse(
        200, {"session_token": "abc", "expires_at": "x", "admin_username": "@admin"}
    )
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/login/verify",
        data={"admin_username": "@admin", "code": "123456"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    set_cookie = response.headers.get("set-cookie", "")
    assert "admin_session=abc" in set_cookie
    assert "HttpOnly" in set_cookie


def test_verify_renders_failure_on_401(fake_api):
    fake_api.routes[("POST", "/admin/login/verify")] = FakeResponse(
        401, {"detail": "invalid_login_code"}
    )
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/login/verify",
        data={"admin_username": "@admin", "code": "000000"},
    )
    assert response.status_code == 401
    assert "Login failed" in response.text


def test_protected_page_redirects_without_cookie(fake_api):
    client = TestClient(web_ui_app)
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_protected_page_redirects_when_api_rejects_token(fake_api):
    fake_api.routes[("GET", "/admin/session/check")] = FakeResponse(
        401, {"detail": "invalid_admin_session"}
    )
    client = TestClient(web_ui_app)
    client.cookies.set("admin_session", "stale-token")
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_dashboard_renders_counts(authed_client, fake_api):
    fake_api.routes[("GET", "/projects")] = FakeResponse(
        200,
        {
            "items": [
                {"id": 1, "slug": "default", "name": "Default", "description": ""},
                {"id": 2, "slug": "billing", "name": "Billing", "description": ""},
            ]
        },
    )
    fake_api.routes[("GET", "/operators")] = FakeResponse(
        200,
        {
            "items": [
                {
                    "id": 1,
                    "username": "@admin",
                    "chat_id": 99,
                    "project_id": 1,
                    "display_name": "",
                    "is_active": True,
                }
            ]
        },
    )
    response = authed_client.get("/admin")
    assert response.status_code == 200
    assert "Projects: 2" in response.text
    assert "Operators: 1" in response.text


def test_projects_list_links_to_details(authed_client, fake_api):
    fake_api.routes[("GET", "/projects")] = FakeResponse(
        200,
        {
            "items": [
                {"id": 1, "slug": "default", "name": "Default", "description": ""},
            ]
        },
    )
    response = authed_client.get("/admin/projects")
    assert response.status_code == 200
    assert "/admin/projects/default" in response.text


def test_projects_new_form(authed_client):
    response = authed_client.get("/admin/projects/new")
    assert response.status_code == 200
    assert "<form action='/admin/projects/new'" in response.text


def test_projects_new_submit_happy_redirects(authed_client, fake_api):
    fake_api.routes[("POST", "/projects")] = FakeResponse(
        200, {"id": 99, "slug": "billing", "name": "B", "description": ""}
    )
    response = authed_client.post(
        "/admin/projects/new",
        data={"slug": "billing", "name": "B", "description": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/projects/billing"


def test_projects_new_submit_failure_renders_error(authed_client, fake_api):
    fake_api.routes[("POST", "/projects")] = FakeResponse(
        409, {"detail": "project_slug_conflict"}
    )
    response = authed_client.post(
        "/admin/projects/new", data={"slug": "x", "name": "x", "description": ""}
    )
    assert response.status_code == 409
    assert "Failed" in response.text


def test_projects_detail_renders_operators(authed_client, fake_api):
    fake_api.routes[("GET", "/projects/billing")] = FakeResponse(
        200,
        {
            "id": 2,
            "slug": "billing",
            "name": "Billing",
            "description": "ops",
            "operator_count": 1,
            "operators": [
                {
                    "username": "@op-a",
                    "chat_id": 1,
                    "is_active": True,
                }
            ],
            "created_at": "x",
            "updated_at": "x",
        },
    )
    response = authed_client.get("/admin/projects/billing")
    assert response.status_code == 200
    assert "Billing" in response.text
    assert "@op-a" in response.text


def test_projects_detail_unknown_returns_404(authed_client, fake_api):
    fake_api.routes[("GET", "/projects/ghost")] = FakeResponse(
        404, {"detail": "project_not_found"}
    )
    response = authed_client.get("/admin/projects/ghost")
    assert response.status_code == 404


def test_projects_edit_submits_to_api(authed_client, fake_api):
    fake_api.routes[("PATCH", "/projects/billing")] = FakeResponse(
        200, {"id": 1, "slug": "billing", "name": "Renamed", "description": "d"}
    )
    response = authed_client.post(
        "/admin/projects/billing/edit",
        data={"name": "Renamed", "description": "d"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/projects/billing"


def test_projects_edit_renders_failure(authed_client, fake_api):
    fake_api.routes[("PATCH", "/projects/billing")] = FakeResponse(
        404, {"detail": "project_not_found"}
    )
    response = authed_client.post(
        "/admin/projects/billing/edit",
        data={"name": "X", "description": ""},
    )
    assert response.status_code == 404


def test_projects_delete(authed_client, fake_api):
    fake_api.routes[("DELETE", "/projects/billing")] = FakeResponse(
        200, {"ok": True}
    )
    response = authed_client.post(
        "/admin/projects/billing/delete", follow_redirects=False
    )
    assert response.status_code == 303


def test_projects_delete_failure(authed_client, fake_api):
    fake_api.routes[("DELETE", "/projects/billing")] = FakeResponse(
        409, {"detail": "project_referenced"}
    )
    response = authed_client.post("/admin/projects/billing/delete")
    assert response.status_code == 409


def test_operators_list(authed_client, fake_api):
    fake_api.routes[("GET", "/operators")] = FakeResponse(
        200,
        {
            "items": [
                {
                    "id": 1,
                    "username": "@op-a",
                    "chat_id": 99,
                    "project_id": 1,
                    "display_name": "Op A",
                    "is_active": True,
                }
            ]
        },
    )
    response = authed_client.get("/admin/operators")
    assert response.status_code == 200
    assert "@op-a" in response.text


def test_operators_new_form(authed_client):
    response = authed_client.get("/admin/operators/new")
    assert response.status_code == 200
    assert "Add operator" in response.text


def test_operators_new_submit(authed_client, fake_api):
    fake_api.routes[("POST", "/operators")] = FakeResponse(
        200,
        {
            "id": 9,
            "username": "@new",
            "chat_id": 7,
            "project_id": 1,
            "display_name": "N",
            "is_active": True,
        },
    )
    response = authed_client.post(
        "/admin/operators/new",
        data={
            "username": "@new",
            "project_id": 1,
            "chat_id": 7,
            "display_name": "N",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_operators_new_submit_failure(authed_client, fake_api):
    fake_api.routes[("POST", "/operators")] = FakeResponse(
        409, {"detail": "operator_username_conflict"}
    )
    response = authed_client.post(
        "/admin/operators/new",
        data={"username": "@dup", "project_id": 1},
    )
    assert response.status_code == 409


def test_operators_edit_form(authed_client, fake_api):
    fake_api.routes[("GET", "/operators/by-username/@op-a")] = FakeResponse(
        200,
        {
            "id": 1,
            "username": "@op-a",
            "chat_id": 7,
            "project_id": 1,
            "display_name": "A",
            "is_active": True,
        },
    )
    response = authed_client.get("/admin/operators/@op-a/edit")
    assert response.status_code == 200
    assert "@op-a" in response.text


def test_operators_edit_form_unknown(authed_client, fake_api):
    fake_api.routes[("GET", "/operators/by-username/@ghost")] = FakeResponse(
        404, {"detail": "operator_not_found"}
    )
    response = authed_client.get("/admin/operators/@ghost/edit")
    assert response.status_code == 404


def test_operators_edit_submit(authed_client, fake_api):
    fake_api.routes[("PATCH", "/operators/@op-a")] = FakeResponse(
        200,
        {
            "id": 1,
            "username": "@op-a",
            "chat_id": 7,
            "project_id": 1,
            "display_name": "A",
            "is_active": False,
        },
    )
    response = authed_client.post(
        "/admin/operators/@op-a/edit",
        data={
            "project_id": "1",
            "chat_id": "7",
            "display_name": "A",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_operators_edit_submit_failure(authed_client, fake_api):
    fake_api.routes[("PATCH", "/operators/@op-a")] = FakeResponse(
        400, {"detail": "project_not_found"}
    )
    response = authed_client.post(
        "/admin/operators/@op-a/edit",
        data={"project_id": "999"},
    )
    assert response.status_code == 400


def test_files_list_renders_project_column(authed_client, fake_api):
    fake_api.routes[("GET", "/knowledge/candidates")] = FakeResponse(
        200,
        {
            "items": [
                {
                    "id": 5,
                    "source_file_name": "doc.pdf",
                    "uploaded_by_operator_username": "@op-a",
                    "project_id": 2,
                }
            ]
        },
    )
    fake_api.routes[("GET", "/projects")] = FakeResponse(
        200,
        {
            "items": [
                {"id": 1, "slug": "default"},
                {"id": 2, "slug": "billing"},
            ]
        },
    )
    response = authed_client.get("/admin/files")
    assert response.status_code == 200
    assert "doc.pdf" in response.text
    assert "billing" in response.text


def test_files_list_no_items(authed_client, fake_api):
    fake_api.routes[("GET", "/knowledge/candidates")] = FakeResponse(
        200, {"items": []}
    )
    fake_api.routes[("GET", "/projects")] = FakeResponse(200, {"items": []})
    response = authed_client.get("/admin/files")
    assert response.status_code == 200
    assert "No files yet" in response.text


def test_files_reassign_happy(authed_client, fake_api):
    fake_api.routes[("POST", "/knowledge/candidates/5/reassign")] = FakeResponse(
        200, {"candidate_id": 5, "project_id": 2}
    )
    response = authed_client.post(
        "/admin/files/5/reassign",
        data={"project_id": 2},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_files_reassign_failure(authed_client, fake_api):
    fake_api.routes[("POST", "/knowledge/candidates/5/reassign")] = FakeResponse(
        400, {"detail": "project_not_found"}
    )
    response = authed_client.post(
        "/admin/files/5/reassign", data={"project_id": 999}
    )
    assert response.status_code == 400


def test_logout_clears_cookie(authed_client, fake_api):
    fake_api.routes[("POST", "/admin/logout")] = FakeResponse(200, {"ok": True})
    response = authed_client.post("/admin/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "admin_session" in set_cookie


def test_admin_root_link_in_shell(fake_api):
    client = TestClient(web_ui_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "/admin/login" in response.text


def test_api_call_non_json_response(monkeypatch):
    """`_api_call` falls back to a synthetic body when api returns non-JSON."""

    class NonJsonResponse:
        status_code = 502
        text = "<html>broken</html>"

        def json(self):
            raise ValueError("not json")

    class NonJsonClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args, **kwargs):
            return None

        async def request(self, *args, **kwargs):
            return NonJsonResponse()

    monkeypatch.setattr(httpx, "AsyncClient", NonJsonClient)

    import asyncio

    status, body = asyncio.run(admin_module._api_call("GET", "/anything"))
    assert status == 502
    assert "broken" in body["detail"]


def test_protected_page_redirects_when_api_returns_no_username(fake_api):
    """If session/check 200s but omits admin_username, treat as anonymous."""
    fake_api.routes[("GET", "/admin/session/check")] = FakeResponse(
        200, {"expires_at": "x"}
    )
    client = TestClient(web_ui_app)
    client.cookies.set("admin_session", "weird-token")
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303


_PROTECTED_GET_ROUTES = [
    "/admin/projects",
    "/admin/projects/new",
    "/admin/projects/billing",
    "/admin/operators",
    "/admin/operators/new",
    "/admin/operators/@op-a/edit",
    "/admin/files",
]

_PROTECTED_POST_ROUTES_WITH_DATA = [
    ("/admin/projects/new", {"slug": "x", "name": "x"}),
    ("/admin/projects/billing/edit", {"name": "Y"}),
    ("/admin/projects/billing/delete", {}),
    ("/admin/operators/new", {"username": "@op", "project_id": "1"}),
    ("/admin/operators/@op-a/edit", {}),
    ("/admin/files/5/reassign", {"project_id": "1"}),
]


@pytest.mark.parametrize("path", _PROTECTED_GET_ROUTES)
def test_protected_get_routes_redirect_without_session(fake_api, path):
    client = TestClient(web_ui_app)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


@pytest.mark.parametrize("path,data", _PROTECTED_POST_ROUTES_WITH_DATA)
def test_protected_post_routes_redirect_without_session(fake_api, path, data):
    client = TestClient(web_ui_app)
    response = client.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
