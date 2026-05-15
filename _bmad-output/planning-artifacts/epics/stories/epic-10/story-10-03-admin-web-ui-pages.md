# Story 10.03 — Admin web UI pages (cookie-gated)

## Objective
Add cookie-gated admin pages to `web_ui` for project, operator, and file management. The cookie carries the opaque session token issued in 10.02; every protected request re-validates it via the api `GET /admin/session/check`. Pages render inline HTML in the existing `services/web_ui/app/main.py` style and forward all mutations to api endpoints.

## Scope

### In Scope
- Auth module `services/web_ui/app/auth.py`:
  - `require_admin(request) -> AdminPrincipal` — FastAPI dependency reading the `admin_session` HttpOnly cookie. On miss/invalid → returns a `RedirectResponse('/admin/login')`. Internally calls api `GET /admin/session/check`.
  - Helpers `set_admin_cookie(response, token, expires_at)` / `clear_admin_cookie(response)`.
- Web UI routes (new files split out for readability):
  - `services/web_ui/app/auth.py`: `GET /admin/login` (form), `POST /admin/login` (request code), `POST /admin/login/verify` (sets cookie), `POST /admin/logout` (clears cookie).
  - `services/web_ui/app/admin_projects.py`: `/admin/projects` (table), `/admin/projects/new` (GET/POST), `/admin/projects/{slug}` (detail), `/admin/projects/{slug}/edit` (GET/POST).
  - `services/web_ui/app/admin_operators.py`: `/admin/operators`, `/admin/operators/new`, `/admin/operators/{username}/edit`.
  - `services/web_ui/app/admin_files.py`: `/admin/files` (paginated table with operator+project filters), `/admin/files/{candidate_id}/reassign` (POST).
- Dashboard at `/admin` showing project count, operator count, recent files, with sidebar nav.
- Update the admin shell at `services/web_ui/app/main.py:71` to:
  - Link to `/admin/login` when no cookie present.
  - Link to `/admin` when authenticated.
- Mount the new routers via `app.include_router` from each new file.

### Out of Scope
- API endpoints themselves are minimal CRUD wrappers (defined in 10.03 alongside web UI? — decision: define `GET|POST /projects`, `GET|PATCH|DELETE /projects/{slug}`, `GET|POST /operators`, `PATCH /operators/{username}`, `GET /operators/by-username/{u}` (internal), `POST /knowledge/candidates/{id}/reassign` here as well, because the web UI needs them. Telegram admin commands in 10.04 share the same endpoints, no new api work needed for that story.)
- Natural-language dialog (10.05).
- RAG scoping (10.06).
- Multi-operator routing in inbound (10.07).
- Pagination beyond a `?limit` query param on file list.

## Implementation Notes
- Cookie attributes: `HttpOnly`, `Secure` only when `settings.app_env == "production"` (allow non-Secure on local), `SameSite=Lax`, `Max-Age = settings.admin_session_ttl_seconds`.
- Forward to api via `httpx.AsyncClient(timeout=settings.operator_upload_api_timeout_seconds)` to match the existing pattern at `services/web_ui/app/main.py:35`.
- HTML rendering: f-string templates inline in route handler, escape user input via `html.escape`.
- Add `_render_nav(active_section)` helper in `auth.py` for shared admin sidebar.

## Test Plan

### Unit / contract
- `tests/test_api_projects_contract.py` — GET list, POST create (duplicate slug 409), PATCH update, DELETE refused when referenced, GET detail 404.
- `tests/test_api_operators_contract.py` — GET list, POST create (unknown project 400), PATCH update, GET by-username 404, GET by-username 200 (internal endpoint — unauthenticated).
- `tests/test_api_knowledge_reassign_contract.py` — POST `/knowledge/candidates/{id}/reassign` updates `project_id` on candidate + on every `rag_chunks` row with matching `source_id`.

### Web UI
- `tests/test_web_ui_admin_auth.py` — login form renders, request-code → cookie not set until verify; verify sets cookie and redirects; protected page redirects to `/admin/login` without cookie; logout clears cookie.
- `tests/test_web_ui_admin_projects.py` — list renders, new form submits, detail shows operators count.
- `tests/test_web_ui_admin_operators.py` — list renders, new form submits, edit toggles is_active.
- `tests/test_web_ui_admin_files.py` — list renders with project column, reassign POST forwards correctly.

## Automated E2E verification
- `tests/e2e/test_e2e_epic10_admin_login.py` (already added in 10.02) extends to assert cookie round-trip via TestClient.
- `tests/e2e/test_e2e_epic10_project_lifecycle.py` — create project + operator via web UI → upload a file → reassign → confirm via api `GET /knowledge/candidates`.

## Manual Verification
1. `docker compose up --build`.
2. Visit `http://localhost/admin` — redirected to `/admin/login`.
3. Enter admin username, click "Send code", receive code in Telegram, paste it → land on `/admin`.
4. Create project "Биллинг" via `/admin/projects/new`.
5. Add operator `@user2` (chat_id `12345`) to "Биллинг" via `/admin/operators/new`.
6. Open `/admin/files`, reassign one file to "Биллинг".
7. Logout → `/admin` again redirects to login.

## Done Criteria
- All unit / contract / e2e tests pass.
- 100% coverage on the new web UI modules and on the new api endpoint handlers.
- `ruff check .` passes.
- Manual flow completes successfully end to end.
