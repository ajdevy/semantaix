from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from services.web_ui.app import main as web_ui_main
from services.web_ui.app.main import app as web_ui_app


@pytest.fixture
def web_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    captured: dict[str, Any] = {"calls": []}

    def make_response(
        *, status_code: int, body: dict | None = None, set_cookie: str | None = None
    ) -> httpx.Response:
        headers = {}
        if set_cookie:
            headers["set-cookie"] = set_cookie
        return httpx.Response(
            status_code=status_code,
            json=body if body is not None else {},
            headers=headers,
        )

    captured["make_response"] = make_response
    captured["routes"] = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args, **kwargs):
            return None

        async def get(self, url: str, **kwargs):
            captured["calls"].append(("GET", url, kwargs))
            handler = captured["routes"].get(("GET", url.split("?", 1)[0]))
            if handler:
                return handler(url=url, **kwargs)
            return make_response(status_code=404, body={"detail": "not_routed"})

        async def post(self, url: str, **kwargs):
            captured["calls"].append(("POST", url, kwargs))
            handler = captured["routes"].get(("POST", url.split("?", 1)[0]))
            if handler:
                return handler(url=url, **kwargs)
            return make_response(status_code=404, body={"detail": "not_routed"})

    monkeypatch.setattr(web_ui_main.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        web_ui_main._settings, "api_internal_base_url", "http://api:8000"
    )
    monkeypatch.setattr(
        web_ui_main._settings, "web_session_cookie_name", "semantaix_session"
    )

    client = TestClient(web_ui_app)
    yield {"client": client, **captured}


def _route(env: dict[str, Any], method: str, path: str, handler) -> None:
    env["routes"][(method, f"http://api:8000{path}")] = handler


def test_login_form_renders(web_env: dict[str, Any]) -> None:
    client: TestClient = web_env["client"]
    response = client.get("/login")
    assert response.status_code == 200
    assert "<form" in response.text
    assert "Telegram" in response.text or "username" in response.text.lower()


def test_login_request_code_proxies_and_renders_paste_form(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/request_code",
        lambda **kw: web_env["make_response"](status_code=200, body={"sent": True}),
    )
    client: TestClient = web_env["client"]
    response = client.post("/login/request_code", data={"username": "@alice"})
    assert response.status_code == 200
    assert "code" in response.text.lower()
    assert "@alice" in response.text


def test_login_request_code_unknown_username_shows_error(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/request_code",
        lambda **kw: web_env["make_response"](
            status_code=404, body={"detail": "username_unknown_or_chat_id_missing"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.post("/login/request_code", data={"username": "@nobody"})
    assert response.status_code == 200
    assert "не зарегистрирован" in response.text


def test_login_verify_sets_cookie_and_redirects(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/verify",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={"username": "@alice", "role": "operator"},
            set_cookie="semantaix_session=abc123; Path=/; HttpOnly",
        ),
    )
    client: TestClient = web_env["client"]
    response = client.post(
        "/login/verify",
        data={"username": "@alice", "code": "123456"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/files"
    cookies = response.headers.get_list("set-cookie")
    assert any("semantaix_session=" in c for c in cookies)


def test_login_verify_expired_code_renders_expired_message(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/verify",
        lambda **kw: web_env["make_response"](
            status_code=410, body={"detail": "expired"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.post(
        "/login/verify", data={"username": "@alice", "code": "123456"}
    )
    assert response.status_code == 200
    assert "устарел" in response.text or "expired" in response.text.lower()


def test_login_verify_invalid_code_renders_error(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/verify",
        lambda **kw: web_env["make_response"](
            status_code=401, body={"detail": "invalid"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.post(
        "/login/verify", data={"username": "@alice", "code": "000000"}
    )
    assert response.status_code == 200
    assert "Неверный" in response.text or "invalid" in response.text.lower()


def test_files_redirects_to_login_when_no_cookie(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=401, body={"detail": "no_session"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.get("/files", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/login"


def test_files_list_renders_table_from_api(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={"username": "@alice", "role": "operator"},
        ),
    )
    _route(
        web_env,
        "GET",
        "/admin/files",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={
                "items": [
                    {
                        "short_id": "ABC2XYZ9",
                        "source_file_name": "policy.pdf",
                        "source_file_type": "pdf",
                        "uploaded_by": "@alice",
                        "uploaded_at": "2026-05-12T09:33:00+00:00",
                        "file_size_bytes": 412000,
                        "is_confidential": True,
                        "kb_ingest_status": "ok",
                        "kb_inserted_chunks": 14,
                        "has_extracted_text": True,
                        "extracted_chars": 38201,
                    }
                ],
                "total": 1,
            },
        ),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files")
    assert response.status_code == 200
    assert "ABC2XYZ9" in response.text
    assert "policy.pdf" in response.text
    assert "🔒" in response.text


def test_files_detail_renders_extracted_text(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={"username": "@alice", "role": "operator"},
        ),
    )
    _route(
        web_env,
        "GET",
        "/admin/files/ABC2XYZ9",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={
                "short_id": "ABC2XYZ9",
                "source_file_name": "policy.pdf",
                "source_file_type": "pdf",
                "uploaded_by": "@alice",
                "uploaded_at": "2026-05-12T09:33:00+00:00",
                "file_size_bytes": 412000,
                "is_confidential": False,
                "kb_ingest_status": "ok",
                "kb_inserted_chunks": 14,
                "candidate_text": "full extracted text body",
            },
        ),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files/ABC2XYZ9")
    assert response.status_code == 200
    assert "ABC2XYZ9" in response.text
    assert "full extracted text body" in response.text


def test_files_detail_404_renders_not_found_page(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={"username": "@alice", "role": "operator"},
        ),
    )
    _route(
        web_env,
        "GET",
        "/admin/files/UNKNOWN1",
        lambda **kw: web_env["make_response"](
            status_code=404, body={"detail": "not_found"}
        ),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files/UNKNOWN1")
    assert response.status_code == 200
    assert "не найден" in response.text.lower() or "not found" in response.text.lower()


def test_files_list_with_search_renders_search_results(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={"username": "@alice", "role": "operator"},
        ),
    )
    _route(
        web_env,
        "GET",
        "/admin/files/search",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={
                "items": [
                    {
                        "short_id": "ABC2XYZ9",
                        "source_file_name": "policy.pdf",
                        "uploaded_by": "@alice",
                        "uploaded_at": "2026-05-12T09:33:00+00:00",
                        "snippet": "…договор между сторонами…",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files", params={"q": "договор"})
    assert response.status_code == 200
    assert "договор" in response.text
    assert "policy.pdf" in response.text


def test_login_request_code_500_renders_generic_error(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/request_code",
        lambda **kw: web_env["make_response"](status_code=503, body={"detail": "x"}),
    )
    client: TestClient = web_env["client"]
    response = client.post("/login/request_code", data={"username": "@alice"})
    assert response.status_code == 200
    assert "Не удалось" in response.text


def test_login_verify_too_many_attempts_renders_lockout(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/verify",
        lambda **kw: web_env["make_response"](
            status_code=429, body={"detail": "too_many_attempts"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.post(
        "/login/verify", data={"username": "@alice", "code": "999999"}
    )
    assert response.status_code == 200
    assert "Слишком много" in response.text


def test_files_detail_redirects_when_no_cookie(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=401, body={"detail": "no_session"}
        ),
    )
    client: TestClient = web_env["client"]
    response = client.get("/files/ABC2XYZ9", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/login"


def test_files_list_admin_owner_filter(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200, body={"username": "@ajdevy", "role": "admin"}
        ),
    )
    captured_params = {}

    def files_handler(url: str, **kw):
        captured_params.update(kw.get("params") or {})
        return web_env["make_response"](
            status_code=200,
            body={
                "items": [
                    {
                        "short_id": "BOB1",
                        "source_file_name": "bob.pdf",
                        "uploaded_by": "@bob",
                        "uploaded_at": "2026-05-12T09:33:00+00:00",
                        "file_size_bytes": 2 * 1024 * 1024,
                        "is_confidential": False,
                        "kb_ingest_status": "ok",
                        "kb_inserted_chunks": 1,
                        "extracted_chars": 100,
                    }
                ],
                "total": 1,
            },
        )

    _route(web_env, "GET", "/admin/files", files_handler)
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "admin-cookie")
    response = client.get("/files", params={"owner": "@bob"})
    assert response.status_code == 200
    assert captured_params.get("owner") == "@bob"
    assert "filter by @owner" in response.text
    assert "2 MB" in response.text


def test_files_list_size_formatting_small_files(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "GET",
        "/admin/auth/me",
        lambda **kw: web_env["make_response"](
            status_code=200, body={"username": "@alice", "role": "operator"}
        ),
    )
    _route(
        web_env,
        "GET",
        "/admin/files",
        lambda **kw: web_env["make_response"](
            status_code=200,
            body={
                "items": [
                    {
                        "short_id": "TINY01",
                        "source_file_name": "tiny.txt",
                        "uploaded_by": "@alice",
                        "uploaded_at": "2026-05-12T09:33:00+00:00",
                        "file_size_bytes": 50,
                        "is_confidential": False,
                        "kb_ingest_status": "ok",
                        "kb_inserted_chunks": 1,
                        "extracted_chars": 10,
                    },
                    {
                        "short_id": "NULL01",
                        "source_file_name": "x.bin",
                        "uploaded_by": "@alice",
                        "uploaded_at": "2026-05-12T09:33:00+00:00",
                        "file_size_bytes": None,
                        "is_confidential": False,
                        "kb_ingest_status": "ok",
                        "kb_inserted_chunks": 0,
                        "extracted_chars": 0,
                    },
                ],
                "total": 2,
            },
        ),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files")
    assert response.status_code == 200
    assert "50 B" in response.text
    assert "—" in response.text


def test_api_get_handles_non_json_response(
    web_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the /me endpoint to return non-JSON.
    def me_handler(url: str, **kw):
        return httpx.Response(
            status_code=200, content=b"<html>not json</html>"
        )

    _route(web_env, "GET", "/admin/auth/me", me_handler)
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.get("/files", follow_redirects=False)
    # 200 returned by /me with garbage body — _resolve_principal still treats
    # status==200 as authenticated, falls back to {"detail": "..."} dict.
    # files_list then proceeds, calls /admin/files which returns 404 default.
    assert response.status_code == 200


def test_logout_clears_cookie_and_redirects(web_env: dict[str, Any]) -> None:
    _route(
        web_env,
        "POST",
        "/admin/auth/logout",
        lambda **kw: web_env["make_response"](status_code=200, body={"ok": True}),
    )
    client: TestClient = web_env["client"]
    client.cookies.set("semantaix_session", "abc123")
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/login"
