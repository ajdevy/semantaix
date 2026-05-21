"""Tests for the web UI admin pages that manage per-project LLM prompts."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from services.web_ui.app.main import app as web_ui_app


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> dict:
        return self._body


class FakeApiClient:
    routes: dict[tuple[str, str], FakeResponse] = {}
    calls: list[dict] = []

    def __init__(self, **_kwargs):
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
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        path = parsed.path or url
        FakeApiClient.calls.append(
            {
                "method": method,
                "path": path,
                "json": json,
                "headers": headers,
            }
        )
        key = (method.upper(), path)
        return FakeApiClient.routes.get(
            key, FakeResponse(404, {"detail": f"route_not_stubbed:{path}"})
        )


@pytest.fixture
def fake_api(monkeypatch):
    FakeApiClient.routes = {}
    FakeApiClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeApiClient)
    return FakeApiClient


@pytest.fixture
def authed_client(fake_api):
    fake_api.routes[("GET", "/admin/session/check")] = FakeResponse(
        200,
        {"admin_username": "@admin", "expires_at": "2099-12-31T00:00:00+00:00"},
    )
    client = TestClient(web_ui_app)
    client.cookies.set("admin_session", "valid-token")
    return client


def test_prompts_list_renders_table_with_items(authed_client, fake_api):
    fake_api.routes[("GET", "/projects/default/prompts")] = FakeResponse(
        200,
        {
            "project_id": 1,
            "project_slug": "default",
            "items": [
                {
                    "prompt_name": "verifier_system",
                    "value": "v" * 200,
                    "version": 3,
                    "updated_by": "@alice",
                    "updated_at": "2026-05-20T10:00:00+00:00",
                    "is_default": False,
                },
                {
                    "prompt_name": "grounding_system",
                    "value": "default text",
                    "version": 0,
                    "updated_by": None,
                    "updated_at": None,
                    "is_default": True,
                },
            ],
        },
    )
    response = authed_client.get("/admin/projects/default/prompts")
    assert response.status_code == 200
    assert "verifier_system" in response.text
    assert "grounding_system" in response.text
    assert "override" in response.text
    assert "default" in response.text


def test_prompts_list_404_when_project_missing(authed_client, fake_api):
    fake_api.routes[("GET", "/projects/ghost/prompts")] = FakeResponse(
        404, {"detail": "project_not_found"}
    )
    response = authed_client.get("/admin/projects/ghost/prompts")
    assert response.status_code == 404
    assert "not found" in response.text


def test_prompts_list_redirects_when_not_logged_in(fake_api):
    client = TestClient(web_ui_app)
    response = client.get(
        "/admin/projects/default/prompts", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_prompt_edit_form_shows_value_and_history(authed_client, fake_api):
    fake_api.routes[
        ("GET", "/projects/default/prompts/verifier_system")
    ] = FakeResponse(
        200,
        {
            "project_id": 1,
            "prompt_name": "verifier_system",
            "value": "current verifier text",
            "version": 2,
            "updated_by": "@alice",
            "updated_at": "2026-05-20T10:00:00+00:00",
            "is_default": False,
            "history": [
                {
                    "version": 2,
                    "value": "current verifier text",
                    "edited_by": "@alice",
                    "created_at": "2026-05-20T10:00:00+00:00",
                },
                {
                    "version": 1,
                    "value": "older text",
                    "edited_by": "@admin",
                    "created_at": "2026-05-19T10:00:00+00:00",
                },
            ],
        },
    )
    response = authed_client.get(
        "/admin/projects/default/prompts/verifier_system"
    )
    assert response.status_code == 200
    assert "current verifier text" in response.text
    assert "Restore" in response.text
    assert "Version history" in response.text


def test_prompt_edit_form_renders_grounding_placeholder_hint(
    authed_client, fake_api
):
    fake_api.routes[
        ("GET", "/projects/default/prompts/grounding_system")
    ] = FakeResponse(
        200,
        {
            "project_id": 1,
            "prompt_name": "grounding_system",
            "value": "Привет {name}, сегодня {today_iso}",
            "version": 1,
            "updated_by": "@alice",
            "updated_at": "2026-05-20T10:00:00+00:00",
            "is_default": False,
            "history": [],
        },
    )
    response = authed_client.get(
        "/admin/projects/default/prompts/grounding_system"
    )
    assert "{name}" in response.text
    assert "{today_iso}" in response.text


def test_prompt_edit_form_404_when_unknown(authed_client, fake_api):
    fake_api.routes[
        ("GET", "/projects/default/prompts/bogus")
    ] = FakeResponse(404, {"detail": "unknown_prompt_name"})
    response = authed_client.get("/admin/projects/default/prompts/bogus")
    assert response.status_code == 404


def test_prompt_save_posts_and_redirects(authed_client, fake_api):
    fake_api.routes[
        ("PUT", "/projects/default/prompts/verifier_system")
    ] = FakeResponse(200, {"version": 3})
    response = authed_client.post(
        "/admin/projects/default/prompts/verifier_system",
        data={"value": "new text"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved_call = next(
        c
        for c in fake_api.calls
        if c["method"] == "PUT"
        and c["path"] == "/projects/default/prompts/verifier_system"
    )
    assert saved_call["json"] == {"value": "new text"}


def test_prompt_save_shows_error_page_on_422(authed_client, fake_api):
    fake_api.routes[
        ("PUT", "/projects/default/prompts/grounding_system")
    ] = FakeResponse(
        422,
        {"detail": "grounding_system: must contain {name} and {today_iso}"},
    )
    response = authed_client.post(
        "/admin/projects/default/prompts/grounding_system",
        data={"value": "missing placeholders"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "Save failed" in response.text


def test_prompt_restore_posts_and_redirects(authed_client, fake_api):
    fake_api.routes[
        ("POST", "/projects/default/prompts/verifier_system/restore")
    ] = FakeResponse(200, {"version": 4})
    response = authed_client.post(
        "/admin/projects/default/prompts/verifier_system/restore",
        data={"version": "2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    call = next(
        c
        for c in fake_api.calls
        if c["method"] == "POST"
        and c["path"] == "/projects/default/prompts/verifier_system/restore"
    )
    assert call["json"] == {"version": 2}


def test_prompt_restore_error_page_on_404(authed_client, fake_api):
    fake_api.routes[
        ("POST", "/projects/default/prompts/verifier_system/restore")
    ] = FakeResponse(404, {"detail": "version_not_found"})
    response = authed_client.post(
        "/admin/projects/default/prompts/verifier_system/restore",
        data={"version": "99"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert "Restore failed" in response.text


def test_prompt_save_redirects_when_not_logged_in(fake_api):
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/projects/default/prompts/verifier_system",
        data={"value": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_prompt_restore_redirects_when_not_logged_in(fake_api):
    client = TestClient(web_ui_app)
    response = client.post(
        "/admin/projects/default/prompts/verifier_system/restore",
        data={"version": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_prompt_edit_form_redirects_when_not_logged_in(fake_api):
    client = TestClient(web_ui_app)
    response = client.get(
        "/admin/projects/default/prompts/verifier_system",
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_project_detail_links_to_prompts(authed_client, fake_api):
    fake_api.routes[("GET", "/projects/default")] = FakeResponse(
        200,
        {
            "id": 1,
            "slug": "default",
            "name": "Default",
            "description": "",
            "operators": [],
            "operator_count": 0,
        },
    )
    response = authed_client.get("/admin/projects/default")
    assert "/admin/projects/default/prompts" in response.text
