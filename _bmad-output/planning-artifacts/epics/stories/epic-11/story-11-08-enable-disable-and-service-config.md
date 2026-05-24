# Story 11.08 — Calendar enable/disable + service config (operator + admin surface)

## Objective
Give a user-facing way to turn the calendar feature on/off and define a project's schedulable services — the entry point FR-20/FR-21 require. **Both the calendar operator and an admin** can enable/disable a project's calendar; **only the operator** may disconnect/delete the integration (admins cannot delete — see FR-18/FR-21). Without this story, 11.07's "connected" path can't be configured in production.

## Scope

### In Scope
- api endpoints (internal, behind `internal_service_token`; admin-gated ones additionally require the Epic-10 admin session/identity):
  - `POST /calendar/projects/{project_id}/enable` — body `{actor, project_timezone, lookahead_days}`; sets `enabled=1` and (if the actor is the operator) records them as the designated calendar operator. Allowed actors: the project's operator **or** an admin.
  - `POST /calendar/projects/{project_id}/disable` — sets `enabled=0`, **keeps** the token. Allowed: operator **or** admin.
  - `GET /calendar/projects/{project_id}/settings` — returns enablement, calendar operator, tz, lookahead, and service rules.
  - `POST /calendar/projects/{project_id}/services` / `DELETE …/services/{id}` — upsert/delete a `calendar_service_rules` row (name, duration, working-hours windows, service-days, date exceptions). Allowed: operator **or** admin.
  - Disconnect/delete remains the **operator-only** path from 11.02/11.03 (no admin route).
- Telegram surface (bot_gateway, extends 11.03 `calendar_commands.py`):
  - Operator: `/calendar_on`, `/calendar_off`, `/calendar_service add|remove …` (Russian help text), gated to the designated operator.
  - Admin: `/calendar_on @projectslug`, `/calendar_off @projectslug` via the existing admin command dispatcher (Epic 10), gated by `admin_telegram_username`. **No admin disconnect command.**
- Authorization helper distinguishing operator-vs-admin and enforcing "admin cannot delete/disconnect".

### Out of Scope
- A web admin UI for calendar settings (deferred; the existing `/admin/*` web surface may add it later).
- Editing rules via natural-language dialog (could mirror epic-10 NL ops in a follow-up).
- The availability computation/answerer (11.05/11.07) and OAuth (11.02).

## Implementation Notes
- Reuse Epic-10 admin auth (`require_admin_session`) for admin routes and the operator-gating pattern for operator routes; do not invent new auth.
- Service-rule JSON shapes match what `compute_availability` (11.05) parses (working-hours windows list, service-days set, date exceptions). Validate on write (reject malformed durations/windows with a clear error).
- All config writes go through `CalendarSettingsRepository` (11.01) via `asyncio.to_thread`.
- Enforce the permission rule centrally: an admin calling any disconnect/delete path → 403; document that disable keeps the token.
- Russian command copy lives with the other calendar command copy.

## Test Plan
### Unit / contract
- `tests/test_api_calendar_config_contract.py` — operator enables (becomes designated operator) / disables (token retained); admin enables/disables; **admin disconnect → 403**; service upsert/list/delete; malformed service rule rejected; `settings` returns the full view.
- `tests/test_bot_gateway_calendar_config_commands.py` — operator `/calendar_on|off|service` gated correctly; admin `/calendar_on @slug` via admin dispatcher; non-authorized → ignored/`unauthorized_calendar`.

### Integration
- Fake `ApiClient` + admin session fixtures (Epic-10 style); fake `TelegramBotSender`.

## Automated E2E verification
- `tests/e2e/test_e2e_epic11_enable_and_configure.py` — admin enables a project + operator defines a service via command → `settings` reflects it → (paired with 11.07) a customer availability question is now answerable. Includes the **admin-cannot-disconnect** assertion. `@pytest.mark.e2e`, `@pytest.mark.epic("11")`.

## Manual Verification
1. Admin: `/calendar_on @projectslug` → project enabled.
2. Operator: `/connect_calendar` (11.03) → connect; `/calendar_service add маникюр 60 mon-sat 10:00-19:00` → service defined.
3. Customer asks availability → answered (with 11.07).
4. Admin: `/calendar_off @projectslug` → disabled, token retained; re-`/calendar_on` → live again.
5. Admin attempts disconnect → rejected (403 / ignored); operator `/disconnect_calendar` → token removed.

## Done Criteria
- 100% coverage on the new endpoints, the auth helper (operator vs admin; admin-cannot-delete branch), and the new commands.
- `ruff check .` passes; `pytest -m e2e` green.
- Permission model enforced: operator+admin enable/disable; operator-only disconnect/delete (admin → 403), verified by tests.
