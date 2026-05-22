# Story 11.03 — Telegram `/connect_calendar` + `/disconnect_calendar` commands

## Objective
Give the designated calendar operator a Telegram entry point: `/connect_calendar` fetches a consent URL from the api and DMs it; `/disconnect_calendar` revokes + deletes the stored token. Gated to authorized operators, mirroring the existing operator-command dispatch (`kb_intent.py`, `/hitl_config`).

## Scope

### In Scope
- `services/bot_gateway/app/calendar_commands.py`:
  - Regex match `^/connect_calendar\b` and `^/disconnect_calendar\b` (case-insensitive), mirroring `_SLASH_RE` in `kb_intent.py`.
  - Resolve the sender's project + operator identity via the existing operator registry (Epic 10); only the project's **designated calendar operator** may run these (non-authorized → ignored with a logged reason `unauthorized_calendar`).
  - `/connect_calendar`: call api `POST /calendar/connect/initiate` (via `ApiClient` + `internal_service_token`), DM the returned consent URL with a short Russian instruction. On api error → DM a Russian "couldn't start, try later".
  - `/disconnect_calendar`: call api `POST /calendar/disconnect`, DM a Russian confirmation.
- Wire the dispatcher into `bot_gateway` message handling alongside the other operator commands; update the operator `/help` text.

### Out of Scope
- The api OAuth endpoints themselves (11.02).
- Token refresh / availability (later stories).
- Letting a non-designated operator connect (single calendar operator per project, v1).

## Implementation Notes
- Reuse the operator-gating pattern already used for `/kb_add` / admin commands; do not invent a new auth path.
- `ApiClient` gets `initiate_calendar_connect(project_id, operator)` and `disconnect_calendar(project_id, operator)` methods using the internal service token.
- All DM copy is Russian and lives as constants near the other command copy (illustrative strings; per Russian-first-content rule keep them with the command, consistent with existing command copy).
- Never log the consent URL's `state` parameter beyond what's necessary; never log tokens.

## Test Plan
### Unit
- `tests/test_bot_gateway_calendar_commands.py` — regex matches connect/disconnect (and flag/case variants); non-designated operator is ignored with `unauthorized_calendar`; designated operator triggers the right `ApiClient` call; api error path DMs the fallback message.

### Integration
- Fake `ApiClient` (capture calls + canned responses) and fake `TelegramBotSender` (capture DMs), per existing bot_gateway command tests.

## Automated E2E verification
- Covered as part of `tests/e2e/test_e2e_epic11_oauth_connect.py` extension: simulate an operator `/connect_calendar` webhook → assert a consent URL DM is sent. `@pytest.mark.e2e`.

## Manual Verification
1. As the designated operator, DM the bot `/connect_calendar` → receive a consent link.
2. As a different (non-designated) user, send `/connect_calendar` → no response; log shows `unauthorized_calendar`.
3. `/disconnect_calendar` → receive a Russian confirmation; token row removed.

## Done Criteria
- 100% coverage on `calendar_commands.py` + the new `ApiClient` methods (incl. unauthorized + api-error branches).
- `ruff check .` passes.
- No tokens / `state` leaked to logs.
