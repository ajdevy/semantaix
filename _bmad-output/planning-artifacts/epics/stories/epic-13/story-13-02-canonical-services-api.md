# Story 13.02 — Canonical `project_services` api + deprecated-endpoint aliases

## Objective
Expose the canonical CRUD HTTP surface for `project_services` (`POST/GET/DELETE /api/projects/{project_id}/services`) on the api service, guarded by `internal_service_token` and the new authorization split (add/edit → `authorize_calendar_config`; remove → new `authorize_service_remove`, operator-only). Convert the old `POST/DELETE /calendar/projects/{id}/services` endpoints into delegating aliases that call the new handlers and emit `deprecation_warning_calendar_services_endpoint` log entries. Endpoint deprecation is **log-only** — the user-facing migration hint DM is owned by the command surface in story 13.03. Architecture reference: lines 232–235, 246; FR reference: FR-23 + FR-24 (admin-gate-narrowing + destructive-op rule).

## Scope

### In Scope
- New routes in `services/api/app/main.py` (or a focused `services_api.py` router file co-located with `admin_files.py`):
  - `POST /api/projects/{project_id}/services` — body fields: `name` (required), `description`, `price_text`, `tags` (list[str] | None), `duration_minutes`, `working_hours` (dict), `service_days` (list), `date_exceptions` (list). Returns the upserted `ProjectService`. Acquires `acquire_service_upsert_lock(project_id, name)` (from 13.01) before dispatching to `to_thread(repo.upsert)`.
  - `GET /api/projects/{project_id}/services` — returns `list[ProjectService]` (all rows, including catalog-only).
  - `DELETE /api/projects/{project_id}/services/{service_id}` — calls `repo.delete`; 404 if not found.
- **Authorization helpers** (`services/api/app/calendar/authorization.py` extended):
  - Existing `authorize_calendar_config(actor_role)` reused for `POST` (add + edit; non-destructive); admin must additionally be a registered project operator (narrower than FR-21 — see decision-log L5).
  - New `authorize_service_remove(actor_role, *, is_registered_project_operator)` — operator-only; admin → 403 `admin_cannot_remove_service` (matches Epic 11's destructive-op rule from FR-18/FR-21). Used by `DELETE`.
  - Both helpers accept actor identity from the existing `internal_service_token`-authenticated request envelope (the bot forwards `actor_username` + `actor_role` as it does for the existing calendar config endpoints — Epic 11 story 11.08 pattern).
- **Old endpoint aliases** (60-day deprecation, removed in Epic 13 cleanup PR):
  - `POST /calendar/projects/{project_id}/services` → calls the new `POST /api/projects/{project_id}/services` handler internally + emits `deprecation_warning_calendar_services_endpoint` with `{endpoint, project_id}`.
  - `DELETE /calendar/projects/{project_id}/services/{service_id}` → calls the new `DELETE` handler + same deprecation log.
  - Aliases preserve the existing request/response shape exactly (callers that still hit the old paths must see no behavioral change beyond the deprecation log).
- **`GET /calendar/projects/{project_id}/settings`** (Epic 11 story 11.08) continues to surface the service rules subset — it now reads via `ProjectServiceRepository.list_calendar_eligible` instead of `CalendarSettingsRepository.list_service_rules` (or via the Epic-11 alias, which delegates per 13.01). No new endpoint here; the read path naturally picks up the canonical store.

### Out of Scope
- The `/service` slash command + `/calendar_service` alias DM hint (story 13.03).
- The NL ops endpoints `POST /api/projects/{id}/services/nl-ops*` (story 13.04).
- The catalog-answerer cutover + prose rendering (story 13.06).
- Any change to authentication for `internal_service_token` itself.

## Implementation Notes
- **Router placement** — follow the Epic 11 11.08 pattern (calendar config endpoints live in `services/api/app/main.py` with helpers in `services/api/app/calendar/`); add the new endpoints next to them so they share the same dependency-injection wiring (`internal_service_token` guard, `ProjectServiceRepository` factory).
- **Lock-then-dispatch pattern:**
  ```
  lock = acquire_service_upsert_lock(project_id, body.name)
  async with lock:
      service = await asyncio.to_thread(repo.upsert, project_id, ...)
  ```
  This serializes same-row writes within one process; the uniqueness constraint catches inter-process races (rare in our single-api-replica deployment but still correct).
- **Validation** — name required + non-empty after strip; if `working_hours` provided, validate the dict shape matches the FR-23 spec (per-weekday list of `[start, end]` strings) — reject with 400 `invalid_working_hours` on malformed; same for `service_days` (must be lowercase 3-letter codes) and `date_exceptions` (ISO date strings). Validation lives in a small `project_services_validation.py` helper next to the repo (kept out of the repo itself to preserve the pure-CRUD seam).
- **Authorization split** — `POST` accepts admin-who-is-also-registered-project-operator; `DELETE` rejects admin even if they ARE a registered project operator (operator-only for destructive ops). The "is registered project operator" check uses the existing Epic 10 `operators` repository.
- **Alias delegation** — the old endpoints are thin wrappers: log the deprecation event, then `await` the new handler. Do NOT duplicate validation / lock acquisition / repo calls — single source of truth.
- **Error shape** — match the existing api error envelope (FastAPI `HTTPException` with `detail={code, message}`); 403 `admin_cannot_remove_service`, 404 `project_service_not_found`, 400 `invalid_working_hours` / `invalid_service_days` / `invalid_date_exceptions` / `invalid_service_name`.

## Test Plan

### Unit
- `tests/test_authorize_service_remove.py` — operator → ok; admin (even if registered project operator) → 403 `admin_cannot_remove_service`; unknown role → 403.
- `tests/test_project_services_validation.py` — accepts the FR-23 JSON shapes; rejects malformed working hours / non-lowercase weekday code / non-ISO date string.

### Contract
- `tests/test_api_project_services_contract.py`:
  - `POST` with operator role → 200; row visible via `GET`.
  - `POST` second time with same name (different case) → updates the same row (single row in `GET`).
  - `POST` with admin-who-is-registered-operator → 200 (add/edit shared).
  - `POST` with admin-NOT-registered → 403.
  - `DELETE` with operator → 200; row gone.
  - `DELETE` with admin (even registered) → 403 `admin_cannot_remove_service`.
  - `DELETE` of unknown service_id → 404.
  - `POST` with malformed `working_hours` → 400 `invalid_working_hours`.
  - Lock single-flight: two concurrent `POST` calls for the same `(project_id, lower(name))` serialize (assert via interleaved fake repo using `asyncio.Event` to detect overlap).
- `tests/test_api_project_services_endpoint_aliases.py`:
  - `POST /calendar/projects/{id}/services` → succeeds + emits `deprecation_warning_calendar_services_endpoint`; same DB state as the new `POST`.
  - `DELETE /calendar/projects/{id}/services/{service_id}` → succeeds + emits deprecation log.
  - Alias deprecation log carries only `{endpoint, project_id}` — no payload values.

### Integration
- Fake `ProjectServiceRepository` (in-memory) + the FastAPI TestClient (Epic-11 11.08 pattern).

## Automated E2E verification
- `tests/e2e/test_e2e_epic13_canonical_api.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("13")`, `@pytest.mark.story("13-02")`): boot api against a fresh `.data/`; `POST` a service as operator; `GET` returns it; `POST` to the deprecated alias as admin (registered) → succeeds + deprecation log captured; `DELETE` as admin → 403; `DELETE` as operator → 200; final `GET` returns empty list.

## Manual Verification
1. `curl -H "Authorization: Bearer $INTERNAL_TOKEN" -X POST .../api/projects/1/services -d '{"name":"маникюр","duration_minutes":60,...}'` as operator → returns the row.
2. Repeat as admin (NOT a registered operator on project 1) → 403.
3. `curl -X DELETE .../api/projects/1/services/{id}` as admin → 403; as operator → 200.
4. `curl -X POST .../calendar/projects/1/services -d '{...}'` (old path) → succeeds; check api logs for `deprecation_warning_calendar_services_endpoint`.

## Done Criteria
- 100% coverage on the new endpoint handlers, the `authorize_service_remove` helper, the validation helper, and the alias-wrapper handlers.
- `ruff check .` passes.
- Authorization split enforced: add/edit shared with admin-who-is-registered-operator; remove is operator-only.
- Single-flight lock asserted by a concurrent-`POST` test.
- Deprecation log emitted on every alias-path call (log-only at the endpoint surface; user-facing DM hint is owned by 13.03).
- Old endpoint aliases preserve request/response shape exactly.
