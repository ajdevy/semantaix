# Story 10.05 — Admin natural-language dialog in the bot

## Objective
Let the admin manage projects, operators, and file assignments through plain Russian phrases sent to the bot in DM. The bot extracts intent + arguments, posts a propose request to api, replies with a preview + confirm token, then commits on a "да"/"подтверждаю"/`/confirm <token>` reply. Mirrors the propose/confirm/cancel pattern from `services/api/app/nl_knowledge_ops.py` but in a separate `admin_nl_op_sessions` table.

## Scope

### In Scope
- Flesh out `services/api/app/admin_nl_ops.py` (`AdminNlOpsRepository`):
  - `propose(admin_username, utterance) -> AdminNlOpSession` — keyword/regex intent extraction:
    - "создай проект <slug> <name…>" → `op_type="project_create"`, payload `{slug, name}`
    - "переименуй проект <slug> в <name…>" → `op_type="project_rename"`, payload `{slug, name}`
    - "добавь оператора <@user> в <slug> [chat_id]" → `op_type="operator_attach"`, payload `{username, project_slug, chat_id}`
    - "удали оператора <@user>" → `op_type="operator_detach"`, payload `{username}`
    - "привяжи файл #<short_id> к <slug>" → `op_type="file_attach"`, payload `{short_id, project_slug}`
    - No match → status `clarify` with a hint string.
  - `confirm(session_id, confirm_token) -> AdminNlOpSession` — dispatches on `op_type` to the corresponding repository (`ProjectRepository`, `OperatorRepository`, etc.). Records final status `confirmed` plus the resulting entity reference.
  - `cancel(session_id) -> AdminNlOpSession`.
  - `get(session_id) -> AdminNlOpSession`.
- New api endpoints:
  - `POST /admin/nl-ops` body `AdminNlOpProposeRequest(admin_username, utterance)` → returns the session (incl. `status`, `preview_text`, `confirm_token` if pending).
  - `POST /admin/nl-ops/{session_id}/confirm` body `AdminNlOpConfirmRequest(confirm_token)`.
  - `POST /admin/nl-ops/{session_id}/cancel`.
  - All gated by `require_admin_or_internal_token`.
- New module `services/bot_gateway/app/admin_nl_dialog.py`:
  - Pure intent detector mirroring the api-side keywords, BUT only used to decide whether to call api `/admin/nl-ops`. Final intent parsing is api-side.
  - Confirm-reply detector: matches "да", "yes", "подтверждаю", "/confirm <token>", "ok". Looks up the most-recent pending session for the admin via api `GET /admin/nl-ops?admin_username=&status=pending_confirmation`, calls confirm.
  - Cancel-reply detector: "нет", "отмена", "cancel", "/cancel <token>" → calls cancel.
- Wire into bot_gateway dispatcher AFTER admin slash commands but BEFORE the standard operator routing: only triggers when sender is admin and message text matches an admin intent or a pending-confirm reply.

### Out of Scope
- LLM-based intent (keyword/regex only).
- RAG scoping (10.06).
- Multi-operator inbound routing (10.07).
- Multi-step clarification dialogs (one round trip only: propose → confirm/cancel).

## Implementation Notes
- Confirm token: `secrets.token_urlsafe(16)`. Stored verbatim in `admin_nl_op_sessions.confirm_token` (no hashing — short-lived, admin-only, and used as part of the bot's own message). `hmac.compare_digest` for verification.
- Sessions auto-expire: a session in `pending_confirmation` older than 10 minutes is treated as `expired` on read.
- Each confirmed op writes an audit row into a new lightweight `admin_nl_op_audit` log (id, session_id, op_type, payload_json, applied_at). Same db file as sessions.

## Test Plan

### Unit
- `tests/test_admin_nl_ops_repository.py` — each `op_type` extraction, unknown utterance → clarify, propose returns confirm_token, confirm dispatches correctly (with fake repositories), confirm with wrong token rejected, expired session rejected, cancel works, audit row written on confirm.

### API contract
- `tests/test_api_admin_nl_ops_contract.py` — propose/confirm/cancel happy paths, replay rejected, unauthorized 401/403, malformed body 400.

### Bot
- `tests/test_bot_gateway_admin_nl_dialog.py` — admin DM "создай проект billing Биллинг" triggers propose → reply with preview + token; admin replies "да" → bot calls confirm; non-admin sender ignored.

## Automated E2E verification
- `tests/e2e/test_e2e_epic10_nl_dialog.py` — end-to-end: admin DMs each supported phrase, confirms each, verifies resulting api state.

## Manual Verification
1. As admin in DM: "создай проект качество для команды контроля качества" → bot replies "Создать проект «качество»? Подтвердите: да / нет (или /confirm <token>)".
2. Reply "да" → bot replies "Проект «качество» создан (id=2)".
3. `/projects` shows the new project.
4. "удали оператора @bob" without prior creation → bot replies "Оператор не найден" or similar; session stored as `clarify`.

## Done Criteria
- All unit + contract + e2e tests pass.
- 100% coverage on `admin_nl_ops.py` and `admin_nl_dialog.py`.
- `ruff check .` passes.
- Existing NL knowledge ops (`/knowledge/nl-ops`) keep working unchanged.
