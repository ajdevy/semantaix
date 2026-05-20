"""Admin web UI pages for Epic 10.

All admin pages live behind the `require_admin` dependency which reads
the `admin_session` cookie set after `/admin/login/verify` and validates
it against the api `/admin/session/check` endpoint. Forms forward to api
CRUD endpoints via httpx using the same cookie value forwarded as the
`X-Admin-Session` header.
"""

from __future__ import annotations

from html import escape as _esc
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from platform_common.settings import get_settings

router = APIRouter()
_settings = get_settings()
_COOKIE_NAME = "admin_session"


def _api_base() -> str:
    return _settings.api_internal_base_url.rstrip("/")


async def _api_call(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    url = f"{_api_base()}{path}"
    async with httpx.AsyncClient(
        timeout=_settings.operator_upload_api_timeout_seconds
    ) as client:
        response = await client.request(
            method, url, json=json_body, headers=headers or {}
        )
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text or "api_returned_non_json"}
    return response.status_code, body


async def require_admin(request: Request) -> str:
    """Return admin_username or raise RedirectResponse on missing/invalid cookie.

    FastAPI's exception model does not allow raising responses, so we
    raise a redirect via a custom exception caught by a higher handler.
    Cleaner: route handlers receive the cookie value via Depends and
    handle the missing case themselves via an early return.
    """
    token = request.cookies.get(_COOKIE_NAME, "")
    if not token:
        return ""
    status, body = await _api_call(
        "GET", "/admin/session/check", headers={"X-Admin-Session": token}
    )
    if status != 200:
        return ""
    return str(body.get("admin_username", ""))


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=303)


def _nav(active: str) -> str:
    def link(href: str, label: str, key: str) -> str:
        if key == active:
            return f"<strong>{label}</strong>"
        return f"<a href='{href}'>{label}</a>"

    return (
        "<nav>"
        + " | ".join(
            [
                link("/admin", "Dashboard", "dashboard"),
                link("/admin/projects", "Projects", "projects"),
                link("/admin/operators", "Operators", "operators"),
                link("/admin/files", "Files", "files"),
                "<form action='/admin/logout' method='post' "
                "style='display:inline'>"
                "<button type='submit'>Logout</button></form>",
            ]
        )
        + "</nav>"
    )


def _page(title: str, body: str, active: str = "") -> str:
    nav = _nav(active) if active else ""
    return (
        "<!doctype html><html><head><title>"
        f"{_esc(title)}"
        "</title></head><body>"
        f"{nav}"
        f"<h1>{_esc(title)}</h1>"
        f"{body}"
        "</body></html>"
    )


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_form() -> str:
    return _page(
        "Admin login",
        f"""
        <form action='/admin/login' method='post'>
          <p><label>Admin username
            <input name='admin_username' value='{
                _esc(_settings.admin_telegram_username)
            }' required />
          </label></p>
          <p><button type='submit'>Send code via Telegram</button></p>
        </form>
        """,
    )


@router.post("/admin/login", response_class=HTMLResponse)
async def admin_login_request(
    admin_username: Annotated[str, Form()],
) -> Response:
    status, body = await _api_call(
        "POST",
        "/admin/login/request",
        json_body={"admin_username": admin_username},
    )
    if status != 200:
        message = _esc(str(body.get("detail", "request_failed")))
        return HTMLResponse(
            _page(
                "Admin login",
                f"<p>Failed to send code (HTTP {status}): {message}.</p>"
                "<p><a href='/admin/login'>Back</a></p>",
            ),
            status_code=status,
        )
    return HTMLResponse(
        _page(
            "Enter login code",
            f"""
            <p>Code sent to your Telegram. Paste it below.</p>
            <form action='/admin/login/verify' method='post'>
              <input type='hidden' name='admin_username'
                     value='{_esc(admin_username)}' />
              <p><label>Code <input name='code' required /></label></p>
              <p><button type='submit'>Sign in</button></p>
            </form>
            """,
        )
    )


@router.post("/admin/login/verify")
async def admin_login_verify(
    admin_username: Annotated[str, Form()],
    code: Annotated[str, Form()],
) -> Response:
    status, body = await _api_call(
        "POST",
        "/admin/login/verify",
        json_body={"admin_username": admin_username, "code": code},
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Admin login",
                f"<p>Login failed (HTTP {status}). "
                "<a href='/admin/login'>Try again</a>.</p>",
            ),
            status_code=status,
        )
    token = str(body["session_token"])
    response: Response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_settings.admin_session_ttl_seconds,
        httponly=True,
        secure=_settings.web_ui_admin_cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/admin/logout")
async def admin_logout(request: Request) -> Response:
    token = request.cookies.get(_COOKIE_NAME, "")
    if token:
        await _api_call(
            "POST",
            "/admin/logout",
            headers={"X-Admin-Session": token},
        )
    response: Response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(_COOKIE_NAME)
    return response


def _auth_headers(request: Request) -> dict[str, str]:
    token = request.cookies.get(_COOKIE_NAME, "")
    return {"X-Admin-Session": token} if token else {}


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request, admin_username: Annotated[str, Depends(require_admin)]
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    headers = _auth_headers(request)
    _, projects_body = await _api_call(
        "GET", "/projects", headers=headers
    )
    _, operators_body = await _api_call(
        "GET", "/operators", headers=headers
    )
    project_count = len(projects_body.get("items", []))
    operator_count = len(operators_body.get("items", []))
    body = (
        f"<p>Signed in as <code>{_esc(admin_username)}</code>.</p>"
        "<ul>"
        f"<li>Projects: {project_count}</li>"
        f"<li>Operators: {operator_count}</li>"
        "</ul>"
    )
    return HTMLResponse(_page("Admin", body, active="dashboard"))


@router.get("/admin/projects", response_class=HTMLResponse)
async def admin_projects_list(
    request: Request, admin_username: Annotated[str, Depends(require_admin)]
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    _, body = await _api_call(
        "GET", "/projects", headers=_auth_headers(request)
    )
    items = body.get("items", [])
    rows = "".join(
        "<tr>"
        f"<td>{p['id']}</td>"
        f"<td><a href='/admin/projects/{_esc(str(p['slug']))}'>"
        f"{_esc(str(p['slug']))}</a></td>"
        f"<td>{_esc(str(p.get('name', '')))}</td>"
        f"<td>{_esc(str(p.get('description') or ''))}</td>"
        "</tr>"
        for p in items
    )
    table = (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>id</th><th>slug</th><th>name</th>"
        "<th>description</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return HTMLResponse(
        _page(
            "Projects",
            table + "<p><a href='/admin/projects/new'>Create new</a></p>",
            active="projects",
        )
    )


@router.get("/admin/projects/new", response_class=HTMLResponse)
async def admin_projects_new_form(
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    return HTMLResponse(
        _page(
            "New project",
            """
            <form action='/admin/projects/new' method='post'>
              <p><label>Slug <input name='slug' required /></label></p>
              <p><label>Name <input name='name' required /></label></p>
              <p><label>Description
                <textarea name='description'></textarea></label></p>
              <p><button type='submit'>Create</button></p>
            </form>
            """,
            active="projects",
        )
    )


@router.post("/admin/projects/new")
async def admin_projects_new_submit(
    request: Request,
    admin_username: Annotated[str, Depends(require_admin)],
    slug: Annotated[str, Form()],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "POST",
        "/projects",
        json_body={
            "slug": slug,
            "name": name,
            "description": description or None,
        },
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "New project",
                f"<p>Failed (HTTP {status}): "
                f"{_esc(str(body.get('detail', '')))}.</p>"
                "<p><a href='/admin/projects/new'>Back</a></p>",
                active="projects",
            ),
            status_code=status,
        )
    return RedirectResponse(
        url=f"/admin/projects/{slug}", status_code=303
    )


@router.get("/admin/projects/{slug}", response_class=HTMLResponse)
async def admin_projects_detail(
    request: Request,
    slug: str,
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "GET",
        f"/projects/{slug}",
        headers=_auth_headers(request),
    )
    if status == 404:
        return HTMLResponse(
            _page(
                "Project not found",
                f"<p>Slug <code>{_esc(slug)}</code> not found.</p>",
                active="projects",
            ),
            status_code=404,
        )
    operators_rows = "".join(
        "<tr>"
        f"<td>{_esc(str(op.get('username', '')))}</td>"
        f"<td>{op.get('chat_id') or '—'}</td>"
        f"<td>{op.get('is_active')}</td>"
        "</tr>"
        for op in body.get("operators", [])
    )
    op_table = (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>username</th><th>chat_id</th>"
        "<th>is_active</th></tr></thead>"
        f"<tbody>{operators_rows}</tbody></table>"
        if operators_rows
        else "<p>No operators yet.</p>"
    )
    detail = (
        f"<dl><dt>ID</dt><dd>{body['id']}</dd>"
        f"<dt>Name</dt><dd>{_esc(str(body.get('name', '')))}</dd>"
        f"<dt>Description</dt><dd>"
        f"{_esc(str(body.get('description') or ''))}</dd>"
        f"<dt>Operators</dt><dd>{body.get('operator_count', 0)}</dd></dl>"
    )
    delete_form = (
        f"<form action='/admin/projects/{_esc(slug)}/delete' method='post' "
        "onsubmit=\"return confirm('Delete project?')\">"
        "<button type='submit'>Delete project</button></form>"
    )
    edit_form = (
        f"<form action='/admin/projects/{_esc(slug)}/edit' method='post'>"
        "<p><label>Name <input name='name' value='"
        f"{_esc(str(body.get('name', '')))}' /></label></p>"
        "<p><label>Description <textarea name='description'>"
        f"{_esc(str(body.get('description') or ''))}"
        "</textarea></label></p>"
        "<p><button type='submit'>Save</button></p></form>"
    )
    prompts_link = (
        f"<p><a href='/admin/projects/{_esc(slug)}/prompts'>"
        "Manage LLM prompts</a></p>"
    )
    return HTMLResponse(
        _page(
            f"Project {body.get('slug', '')}",
            detail + op_table + prompts_link + edit_form + delete_form,
            active="projects",
        )
    )


@router.post("/admin/projects/{slug}/edit")
async def admin_projects_edit_submit(
    request: Request,
    slug: str,
    admin_username: Annotated[str, Depends(require_admin)],
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    payload: dict[str, object] = {}
    if name:
        payload["name"] = name
    payload["description"] = description or None
    status, _ = await _api_call(
        "PATCH",
        f"/projects/{slug}",
        json_body=payload,
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Project edit failed",
                f"<p>Edit failed (HTTP {status}).</p>"
                f"<p><a href='/admin/projects/{_esc(slug)}'>Back</a></p>",
                active="projects",
            ),
            status_code=status,
        )
    return RedirectResponse(
        url=f"/admin/projects/{slug}", status_code=303
    )


@router.post("/admin/projects/{slug}/delete")
async def admin_projects_delete(
    request: Request,
    slug: str,
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, _ = await _api_call(
        "DELETE",
        f"/projects/{slug}",
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Project delete failed",
                f"<p>Delete failed (HTTP {status}).</p>"
                f"<p><a href='/admin/projects/{_esc(slug)}'>Back</a></p>",
                active="projects",
            ),
            status_code=status,
        )
    return RedirectResponse(url="/admin/projects", status_code=303)


@router.get("/admin/operators", response_class=HTMLResponse)
async def admin_operators_list(
    request: Request, admin_username: Annotated[str, Depends(require_admin)]
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    _, body = await _api_call(
        "GET", "/operators", headers=_auth_headers(request)
    )
    items = body.get("items", [])
    rows = "".join(
        "<tr>"
        f"<td>{op['id']}</td>"
        f"<td><a href='/admin/operators/{_esc(str(op['username']))}/edit'>"
        f"{_esc(str(op['username']))}</a></td>"
        f"<td>{_esc(str(op.get('chat_id') or '—'))}</td>"
        f"<td>{op['project_id']}</td>"
        f"<td>{op['is_active']}</td>"
        "</tr>"
        for op in items
    )
    table = (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>id</th><th>username</th><th>chat_id</th>"
        "<th>project_id</th><th>active</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return HTMLResponse(
        _page(
            "Operators",
            table + "<p><a href='/admin/operators/new'>Add operator</a></p>",
            active="operators",
        )
    )


@router.get("/admin/operators/new", response_class=HTMLResponse)
async def admin_operators_new_form(
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    return HTMLResponse(
        _page(
            "Add operator",
            """
            <form action='/admin/operators/new' method='post'>
              <p><label>Username (with @)
                <input name='username' required /></label></p>
              <p><label>Project ID
                <input name='project_id' type='number' required /></label></p>
              <p><label>Chat ID
                <input name='chat_id' type='number' /></label></p>
              <p><label>Display name
                <input name='display_name' /></label></p>
              <p><button type='submit'>Create</button></p>
            </form>
            """,
            active="operators",
        )
    )


@router.post("/admin/operators/new")
async def admin_operators_new_submit(
    request: Request,
    admin_username: Annotated[str, Depends(require_admin)],
    username: Annotated[str, Form()],
    project_id: Annotated[int, Form()],
    chat_id: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    payload: dict[str, object] = {
        "username": username,
        "project_id": project_id,
    }
    if chat_id:
        payload["chat_id"] = int(chat_id)
    if display_name:
        payload["display_name"] = display_name
    status, body = await _api_call(
        "POST",
        "/operators",
        json_body=payload,
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Operator create failed",
                f"<p>Failed (HTTP {status}): "
                f"{_esc(str(body.get('detail', '')))}.</p>"
                "<p><a href='/admin/operators/new'>Back</a></p>",
                active="operators",
            ),
            status_code=status,
        )
    return RedirectResponse(url="/admin/operators", status_code=303)


@router.get(
    "/admin/operators/{username:path}/edit", response_class=HTMLResponse
)
async def admin_operators_edit_form(
    request: Request,
    username: str,
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "GET",
        f"/operators/by-username/{username}",
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Operator not found",
                f"<p>Username <code>{_esc(username)}</code> not found.</p>",
                active="operators",
            ),
            status_code=404,
        )
    return HTMLResponse(
        _page(
            f"Edit {body['username']}",
            f"""
            <form action='/admin/operators/{_esc(username)}/edit'
                  method='post'>
              <p><label>Project ID
                <input name='project_id' type='number'
                       value='{body['project_id']}' /></label></p>
              <p><label>Chat ID
                <input name='chat_id' type='number'
                       value='{_esc(str(body.get('chat_id') or ''))}'
                       /></label></p>
              <p><label>Display name
                <input name='display_name'
                       value='{_esc(str(body.get('display_name') or ''))}'
                       /></label></p>
              <p><label>Active
                <input name='is_active' type='checkbox' value='true'
                       {'checked' if body.get('is_active') else ''} />
              </label></p>
              <p><button type='submit'>Save</button></p>
            </form>
            """,
            active="operators",
        )
    )


@router.post("/admin/operators/{username:path}/edit")
async def admin_operators_edit_submit(
    request: Request,
    username: str,
    admin_username: Annotated[str, Depends(require_admin)],
    project_id: Annotated[str, Form()] = "",
    chat_id: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    is_active: Annotated[str, Form()] = "",
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    payload: dict[str, object] = {"is_active": bool(is_active)}
    if project_id:
        payload["project_id"] = int(project_id)
    if chat_id:
        payload["chat_id"] = int(chat_id)
    if display_name:
        payload["display_name"] = display_name
    status, _ = await _api_call(
        "PATCH",
        f"/operators/{username}",
        json_body=payload,
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Operator edit failed",
                f"<p>Edit failed (HTTP {status}).</p>"
                f"<p><a href='/admin/operators/{_esc(username)}/edit'>"
                "Back</a></p>",
                active="operators",
            ),
            status_code=status,
        )
    return RedirectResponse(url="/admin/operators", status_code=303)


@router.get("/admin/files", response_class=HTMLResponse)
async def admin_files_list(
    request: Request, admin_username: Annotated[str, Depends(require_admin)]
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    headers = _auth_headers(request)
    _, candidates_body = await _api_call(
        "GET", "/knowledge/candidates", headers=headers
    )
    _, projects_body = await _api_call(
        "GET", "/projects", headers=headers
    )
    project_slug_by_id = {
        int(p["id"]): str(p["slug"])
        for p in projects_body.get("items", [])
    }
    rows = []
    for c in candidates_body.get("items", []):
        project_id = c.get("project_id")
        project_label = (
            project_slug_by_id.get(int(project_id), f"#{project_id}")
            if project_id is not None
            else "—"
        )
        reassign = (
            f"<form action='/admin/files/{c['id']}/reassign' method='post' "
            "style='display:inline'>"
            "<select name='project_id'>"
            + "".join(
                f"<option value='{p['id']}' "
                f"{'selected' if p['id'] == project_id else ''}>"
                f"{_esc(str(p['slug']))}</option>"
                for p in projects_body.get("items", [])
            )
            + "</select><button type='submit'>Move</button></form>"
        )
        rows.append(
            "<tr>"
            f"<td>{c['id']}</td>"
            f"<td>{_esc(str(c.get('source_file_name') or '—'))}</td>"
            f"<td>{_esc(str(c.get('uploaded_by_operator_username') or '—'))}"
            "</td>"
            f"<td>{_esc(project_label)}</td>"
            f"<td>{reassign}</td>"
            "</tr>"
        )
    table = (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>id</th><th>file</th><th>operator</th>"
        "<th>project</th><th>reassign</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows
        else "<p>No files yet.</p>"
    )
    return HTMLResponse(_page("Files", table, active="files"))


@router.post("/admin/files/{candidate_id}/reassign")
async def admin_files_reassign(
    request: Request,
    candidate_id: int,
    admin_username: Annotated[str, Depends(require_admin)],
    project_id: Annotated[int, Form()],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, _ = await _api_call(
        "POST",
        f"/knowledge/candidates/{candidate_id}/reassign",
        json_body={"project_id": project_id},
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Reassign failed",
                f"<p>Reassign failed (HTTP {status}).</p>"
                "<p><a href='/admin/files'>Back</a></p>",
                active="files",
            ),
            status_code=status,
        )
    return RedirectResponse(url="/admin/files", status_code=303)


def _prompt_preview(value: str, *, limit: int = 80) -> str:
    flat = value.replace("\n", " ").strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@router.get(
    "/admin/projects/{slug}/prompts", response_class=HTMLResponse
)
async def admin_project_prompts_list(
    request: Request,
    slug: str,
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "GET",
        f"/projects/{slug}/prompts",
        headers=_auth_headers(request),
    )
    if status == 404:
        return HTMLResponse(
            _page(
                "Project not found",
                f"<p>Slug <code>{_esc(slug)}</code> not found.</p>",
                active="projects",
            ),
            status_code=404,
        )
    items = body.get("items", [])
    rows = "".join(
        "<tr>"
        f"<td><a href='/admin/projects/{_esc(slug)}/prompts/"
        f"{_esc(str(item['prompt_name']))}'>"
        f"{_esc(str(item['prompt_name']))}</a></td>"
        f"<td>{_esc(_prompt_preview(str(item.get('value', ''))))}</td>"
        f"<td>{item.get('version', 0)}</td>"
        f"<td>{_esc(str(item.get('updated_by') or '—'))}</td>"
        f"<td>{_esc(str(item.get('updated_at') or '—'))}</td>"
        f"<td>{'default' if item.get('is_default') else 'override'}</td>"
        "</tr>"
        for item in items
    )
    table = (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>name</th><th>preview</th><th>v</th>"
        "<th>updated by</th><th>updated at</th>"
        "<th>source</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return HTMLResponse(
        _page(
            f"Prompts — project {slug}",
            f"<p><a href='/admin/projects/{_esc(slug)}'>← Back to project</a>"
            f"</p>{table}",
            active="projects",
        )
    )


@router.get(
    "/admin/projects/{slug}/prompts/{name}", response_class=HTMLResponse
)
async def admin_project_prompt_edit_form(
    request: Request,
    slug: str,
    name: str,
    admin_username: Annotated[str, Depends(require_admin)],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "GET",
        f"/projects/{slug}/prompts/{name}",
        headers=_auth_headers(request),
    )
    if status == 404:
        return HTMLResponse(
            _page(
                "Prompt not found",
                f"<p>{_esc(slug)} / {_esc(name)} not found.</p>",
                active="projects",
            ),
            status_code=404,
        )
    hint = (
        "<p><em>Must contain <code>{name}</code> and "
        "<code>{today_iso}</code> placeholders.</em></p>"
        if name == "grounding_system"
        else ""
    )
    history_rows = "".join(
        "<tr>"
        f"<td>{item['version']}</td>"
        f"<td>{_esc(str(item.get('edited_by', '')))}</td>"
        f"<td>{_esc(str(item.get('created_at', '')))}</td>"
        "<td>"
        f"<form action='/admin/projects/{_esc(slug)}/prompts/"
        f"{_esc(name)}/restore' method='post' style='display:inline'>"
        f"<input type='hidden' name='version' value='{item['version']}' />"
        "<button type='submit'>Restore</button></form>"
        "</td></tr>"
        for item in body.get("history", [])
    )
    history_table = (
        "<h2>Version history</h2>"
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>v</th><th>edited by</th><th>created at</th>"
        "<th>action</th></tr></thead>"
        f"<tbody>{history_rows}</tbody></table>"
        if history_rows
        else "<p>No prior versions.</p>"
    )
    edit_form = (
        f"<form action='/admin/projects/{_esc(slug)}/prompts/{_esc(name)}' "
        "method='post'>"
        f"<p><textarea name='value' rows='20' cols='100'>"
        f"{_esc(str(body.get('value', '')))}</textarea></p>"
        "<p><button type='submit'>Save</button></p></form>"
    )
    meta = (
        f"<p>Source: <strong>"
        f"{'default' if body.get('is_default') else 'override'}</strong>; "
        f"version <code>{body.get('version', 0)}</code></p>"
    )
    back = (
        f"<p><a href='/admin/projects/{_esc(slug)}/prompts'>"
        "← Back to prompts</a></p>"
    )
    return HTMLResponse(
        _page(
            f"{name} — project {slug}",
            back + meta + hint + edit_form + history_table,
            active="projects",
        )
    )


@router.post("/admin/projects/{slug}/prompts/{name}")
async def admin_project_prompt_save(
    request: Request,
    slug: str,
    name: str,
    admin_username: Annotated[str, Depends(require_admin)],
    value: Annotated[str, Form()] = "",
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, body = await _api_call(
        "PUT",
        f"/projects/{slug}/prompts/{name}",
        json_body={"value": value},
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Save failed",
                f"<p>Save failed (HTTP {status}): "
                f"{_esc(str(body.get('detail', '')))}.</p>"
                f"<p><a href='/admin/projects/{_esc(slug)}/prompts/"
                f"{_esc(name)}'>Back</a></p>",
                active="projects",
            ),
            status_code=status,
        )
    return RedirectResponse(
        url=f"/admin/projects/{slug}/prompts/{name}", status_code=303
    )


@router.post("/admin/projects/{slug}/prompts/{name}/restore")
async def admin_project_prompt_restore(
    request: Request,
    slug: str,
    name: str,
    admin_username: Annotated[str, Depends(require_admin)],
    version: Annotated[int, Form()],
) -> Response:
    if not admin_username:
        return _redirect_to_login()
    status, _ = await _api_call(
        "POST",
        f"/projects/{slug}/prompts/{name}/restore",
        json_body={"version": version},
        headers=_auth_headers(request),
    )
    if status != 200:
        return HTMLResponse(
            _page(
                "Restore failed",
                f"<p>Restore failed (HTTP {status}).</p>"
                f"<p><a href='/admin/projects/{_esc(slug)}/prompts/"
                f"{_esc(name)}'>Back</a></p>",
                active="projects",
            ),
            status_code=status,
        )
    return RedirectResponse(
        url=f"/admin/projects/{slug}/prompts/{name}", status_code=303
    )
