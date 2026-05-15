# Story 10.04 — Admin Telegram slash commands

## Objective
Expose admin-only Telegram slash commands in `bot_gateway` that drive the same api endpoints as the web UI. Commands are gated by `settings.admin_telegram_username`. They sit alongside the existing operator-only commands and the existing `/hitl_config` admin command, fitting into the message-routing chain in `_process_telegram_update`.

## Scope

### In Scope
- New module `services/bot_gateway/app/admin_commands.py`:
  - Pure async handlers, each `async def handle_X(*, normalized, api_client) -> dict[str, str]`. Sends the formatted reply via `bot_sender` injected through `api_client` or a passed `send_dm` helper (match the pattern in `services/bot_gateway/app/main.py:1047`).
  - Trigger regexes (`re.IGNORECASE`):
    - `_PROJECTS_LIST_RE = r"^\s*/projects(\s|$)"` → `handle_list_projects`
    - `_PROJECT_NEW_RE = r"^\s*/project_new\s+(?P<slug>\S+)\s+(?P<name>.+)$"` → `handle_create_project`
    - `_OPERATOR_ADD_RE = r"^\s*/operator_add\s+(?P<username>@\S+)\s+(?P<project_slug>\S+)(?:\s+(?P<chat_id>\d+))?\s*$"` → `handle_add_operator`
    - `_OPERATOR_REMOVE_RE = r"^\s*/operator_remove\s+(?P<username>@\S+)\s*$"` → `handle_remove_operator`
    - `_OPERATOR_LIST_RE = r"^\s*/operator_list(\s|$)"` → `handle_list_operators`
    - `_FILE_ASSIGN_RE = r"^\s*/file_assign\s+#(?P<short_id>\S+)\s+(?P<project_slug>\S+)\s*$"` → `handle_file_assign`
- Dispatcher `async def handle_admin_project_command(normalized, api_client) -> dict[str, str] | None`:
  - Returns `None` if `normalized.username != settings.admin_telegram_username` so the rest of the routing chain proceeds untouched.
  - Otherwise tries each trigger regex in order; first match wins.
  - Unknown `/<command>` from the admin → returns `{"status": "ignored", "reason": "admin_unknown_command"}` (keeps the chain unblocked).
- Extend `ApiClient` (`services/bot_gateway/app/api_client.py`) with internal-token-authenticated wrappers:
  - `list_projects()`, `create_project(slug, name, description=None)`
  - `list_operators()`, `attach_operator(username, project_slug, chat_id=None, display_name=None)`, `detach_operator(username)` (PATCH `is_active=false`)
  - `reassign_file(short_id, project_slug)` — resolves `short_id` (operator-file id) to a knowledge candidate via a new internal api lookup `GET /knowledge/candidates/by-operator-file/{short_id}`, then POSTs `/knowledge/candidates/{id}/reassign`.
- `bot_gateway` reads `settings.admin_internal_token` (new setting, used for bot↔api server-to-server auth bypassing admin session cookies) and includes it as `X-Internal-Token` on these calls. Api validates via a `require_internal_token` dependency on each admin endpoint, OR on the new `/operators/by-username/{u}` and `/knowledge/candidates/by-operator-file/{short_id}` (so the bot doesn't need an admin session). Decision: api admin endpoints accept EITHER `X-Admin-Session` OR `X-Internal-Token`.
- Wire `handle_admin_project_command` into `_process_telegram_update` between `_handle_admin_hitl_command` and `_handle_whoami_command` (`services/bot_gateway/app/main.py:~1456`).

### Out of Scope
- Natural-language dialog (10.05).
- RAG scoping (10.06).
- Multi-operator inbound routing (10.07).

## Implementation Notes
- Output formatting: similar table style to `_handle_files_command` at `services/bot_gateway/app/main.py:1047`. Project list line: `📁 #{id} · {slug} · {name}`. Operator list line: `👤 {username} → #{project_id} · is_active={true/false}`.
- Reuse `_send_dm` from existing bot_gateway code for replies; respect 4096-char Telegram limit (truncate gracefully if list exceeds).
- The dispatch order matters: `_handle_admin_hitl_command` continues to handle `/hitl_config` before this new dispatcher.

## Test Plan

### Unit
- `tests/test_bot_gateway_admin_commands.py` — happy path for each command (fake `ApiClient` records calls), non-admin sender returns `None`, malformed regex args return helpful reply, `_FILE_ASSIGN_RE` accepts both pure-digit and alphanumeric short_id.

### API contract additions
- `tests/test_api_internal_token_auth.py` — `X-Internal-Token` accepted in lieu of admin session for the affected endpoints; missing/invalid token → 401.
- `tests/test_api_knowledge_candidates_by_operator_file.py` — lookup hits + 404 misses.

## Automated E2E verification
- `tests/e2e/test_e2e_epic10_admin_telegram.py` — drive the bot dispatcher with a sequence of admin DMs covering project_new, operator_add, operator_list, file_assign. Verify resulting api state.

## Manual Verification
1. As admin in Telegram DM with the bot, send `/projects` — receive list including "default".
2. `/project_new billing Биллинг команда` — receive ack, then re-running `/projects` shows the row.
3. `/operator_add @user2 billing 12345` — receive ack.
4. `/operator_list` — shows the two operators with project ids.
5. `/files 5` (existing operator command) lists files. `/file_assign #<short_id> billing` — receive ack, then `/files` shows status `ok/ok` (no re-ingest, just reassign).

## Done Criteria
- All unit + contract + e2e tests pass.
- 100% coverage on `services/bot_gateway/app/admin_commands.py` and on the new `ApiClient` methods.
- `ruff check .` passes.
- Existing `/hitl_config`, `/kb_add`, `/files`, `/send` commands continue to behave unchanged.
