# Story 11.08 — Calendar disable + service config (operator + admin surface)

## Objective
Give a user-facing way to **disable** the calendar feature for a project (without losing the connected token) and to **define a project's schedulable services** — the entry points FR-20/FR-21 require beyond the OAuth connect flow. **Enablement is implicit in `/connect_calendar`** (see story 11.02): a successful OAuth callback flips `enabled=1` and records the connecting operator as the designated calendar operator atomically with the token upsert. There is no separate `/calendar_on` operator command or `POST /enable` endpoint. **Both the calendar operator and an admin** may disable a project's calendar and define services; **only the operator** may disconnect/delete the integration. **Re-enable after `/calendar_off` means the operator re-runs `/connect_calendar`.**

> Historical note (PR #75 follow-up): this story originally shipped with a standalone `POST /calendar/projects/{id}/enable` endpoint plus operator `/calendar_on` and admin `/calendar_on @slug` commands. The directive "we don't need a separate /calendar_on command, just make it on when using /connect_calendar" replaced that surface with the implicit auto-enable inside the OAuth callback. Disable + service config remain as before.

## Scope

### In Scope
- api endpoints (internal, behind `internal_service_token`; admin-gated ones additionally require the Epic-10 admin session/identity):
  - `POST /calendar/projects/{project_id}/disable` — sets `enabled=0`, **keeps** the token. Allowed: operator **or** admin.
  - `GET /calendar/projects/{project_id}/settings` — returns enablement, calendar operator, tz, lookahead, and service rules.
  - `POST /calendar/projects/{project_id}/services` / `DELETE …/services/{id}` — upsert/delete a `calendar_service_rules` row (name, duration, working-hours windows, service-days, date exceptions). Allowed: operator **or** admin.
  - Disconnect/delete remains the **operator-only** path from 11.02/11.03 (no admin route).
  - **Auto-enable inside `GET /calendar/oauth/callback`** (the only enable path): on successful state validation + code exchange + token upsert, the callback calls `CalendarSettingsRepository.enable(...)` so the project becomes enabled with the connecting operator recorded as the designated calendar operator. For an already-enabled project, the existing `project_timezone` / `lookahead_days` are preserved (only the designated operator may change). If `enable` raises after the token upsert succeeds, the callback returns a 500-class error and does **not** render the success page (the failure is logged with project + operator context; the operator retries by re-running `/connect_calendar`).
- Telegram surface (bot_gateway, extends 11.03 `calendar_commands.py`):
  - Operator: `/calendar_off`, `/calendar_service add|remove …` (Russian help text), gated to the designated operator. There is no `/calendar_on` operator command.
  - Admin: `/calendar_off @projectslug` via the existing admin command dispatcher (Epic 10), gated by `admin_telegram_username`. **No admin enable command and no admin disconnect command.**
- Authorization helper distinguishing operator-vs-admin and enforcing "admin cannot delete/disconnect".

### Out of Scope
- A web admin UI for calendar settings (deferred; the existing `/admin/*` web surface may add it later).
- Editing rules via natural-language dialog (could mirror epic-10 NL ops in a follow-up).
- The availability computation/answerer (11.05/11.07) and OAuth (11.02).
- A standalone enable endpoint or command — superseded by the implicit auto-enable in the OAuth callback.

## Implementation Notes
- Reuse Epic-10 admin auth (`require_admin_session`) for admin routes and the operator-gating pattern for operator routes; do not invent new auth.
- Service-rule JSON shapes match what `compute_availability` (11.05) parses (working-hours windows list, service-days set, date exceptions). Validate on write (reject malformed durations/windows with a clear error).
- All config writes go through `CalendarSettingsRepository` (11.01) via `asyncio.to_thread`.
- Enforce the permission rule centrally: an admin calling any disconnect/delete path → 403; document that disable keeps the token.
- The OAuth callback's auto-enable runs after the token upsert and is **best-effort atomic**: if `enable` fails, log + return a 500-class HTML (so the operator doesn't silently land in a half-state where the token is stored but the project is still disabled).
- Russian command copy lives with the other calendar command copy.

## Test Plan
### Unit / contract
- `tests/test_api_calendar_config_contract.py` — disable (operator + admin) keeps the stored token; admin disconnect → 403; unknown actor_role → 403; service upsert/list/delete; malformed service rule rejected; `settings` returns the full view. **No `/enable` endpoint tests.**
- `tests/test_api_calendar_oauth_contract.py` — auto-enable inside the OAuth callback:
  - fresh project → `is_enabled` is True and the designated `calendar_operator` is the connecting operator; defaults applied;
  - already-enabled project → existing `project_timezone` / `lookahead_days` are preserved; the designated operator becomes the connecting one;
  - `settings_repo.enable` raises after `token_repo.upsert` → callback returns 500 and does not render the success page; failure logged with `trace_id`-style context (`project_id`, `operator`).
- `tests/test_bot_gateway_calendar_config_commands.py` — operator `/calendar_off|service` gated correctly; admin `/calendar_off @slug` via admin dispatcher; non-authorized → ignored/`unauthorized_calendar`. **No `/calendar_on` tests (operator or admin).**

### Integration
- Fake `ApiClient` + admin session fixtures (Epic-10 style); fake `TelegramBotSender`.

## Automated E2E verification
- `tests/e2e/test_e2e_epic11_enable_and_configure.py` — operator connects via the OAuth callback (Google mocked) → project enabled + designated operator recorded → operator defines a service → `settings` reflects it → admin `/calendar_off` keeps the token → re-`/connect_calendar` re-enables → admin disconnect → 403 (operator-only) → operator disconnect deletes the token. `@pytest.mark.e2e`, `@pytest.mark.epic("11")`.

## Manual Verification
1. Operator: `/connect_calendar` → consent on Google → project enabled with the operator as designated calendar operator + token stored; bot DM confirmation.
2. Operator: `/calendar_service add маникюр 60 mon-sat 10:00-19:00` → service defined.
3. Customer asks availability → answered (with 11.07).
4. Admin: `/calendar_off @projectslug` → disabled, token retained.
5. Re-enable: operator re-runs `/connect_calendar` → re-flips `enabled=1` and overwrites the token; `project_timezone` / `lookahead_days` preserved.
6. Admin attempts disconnect → rejected (403 / ignored); operator `/disconnect_calendar` → token removed.

## Done Criteria
- 100% coverage on the disable + service-rule endpoints, the auth helper (operator vs admin; admin-cannot-delete branch), the new commands, and the auto-enable branch of the OAuth callback (happy path + already-enabled preservation + enable-failure-after-token-upsert).
- `ruff check .` passes; `pytest -m e2e` green.
- Permission model enforced: enable only via `/connect_calendar`; operator+admin disable; operator-only disconnect/delete (admin → 403). All three verified by tests.
