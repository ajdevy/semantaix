# Story 12.02 — Service commands and `/sales_state`

## Objective
Ship the Telegram operator commands that populate the `services` catalog and surface conversation state, with minimal-arg ergonomics and the existing operator/admin gating. With this story merged, an operator can seed services from Telegram — which is what flips the data-driven activation gate for a project (no `/sales_on` is ever needed).

## Scope

### In Scope
- New api endpoints (internal, service-token-gated; mirror existing `/admin/*` shape):
  - `POST /sales/services` `{project_id, name, description_md?, tags?}` → `{id}`. `409` on `ServiceAlreadyExists`.
  - `GET /sales/services?project_id=` → `{services: [Service]}`.
  - `DELETE /sales/services/{id}` → `{ok: true}`.
  - `GET /sales/state?project_id=&chat_id=` → `{states: [ConversationState]}` (chat_id optional; absent → all active).
- New bot_gateway slash-command handlers (gated identically to `/kb_add` / Epic-09 commands — only the project's effective operator OR an admin per Epic-10 may invoke; unauthorized senders ignored with a logged `reason="unauthorized_sales_command"`):
  - `/service_add <name>` — optional `| description` after a literal `|` pipe. Examples:
    - `/service_add каньонинг` → name only.
    - `/service_add каньонинг | Каньонинг — это…` → name + description.
    - Resolves `project_id` from the operator's mapping (Epic 10), calls `POST /sales/services`, echoes `Добавлено: каньонинг (id=12)` on success or a one-line validation error.
  - `/service_list` — calls `GET /sales/services`, echoes one row per service: `12. каньонинг — Каньонинг — это…`. Empty list → `Услуг пока нет. Добавьте первую через /service_add <название>.`
  - `/service_remove <id>` — calls `DELETE`; echoes `Удалено: id=12` or `Не найдено: id=12`.
  - `/sales_state` — calls `GET /sales/state`; with `@customer` arg, filters server-side to that chat (resolved via `operator_chat_lookup`). Output is a compact one-line-per-chat summary: `chat=12345 stage=scoping intent={"dates":"1 мая"} last_msg=18:42`.
- Argument parsing helpers in `services/bot_gateway/app/sales_commands.py` (new module): `parse_service_add(text) -> (name, description | None)` (splits on first `|`, trims both sides, rejects empty name), `parse_service_remove(text) -> int` (validates the id is a positive integer), `parse_sales_state(text) -> str | None` (returns `@username` or `None`).
- Validation-error UX: every command returns exactly one line on bad input, with the canonical usage example (e.g. `Использование: /service_add <название> [| описание]`).

### Out of Scope
- `/material*` commands — story 12.05 (KB upload also feeds `client_materials` via 12.05b).
- `/sales_on` / `/sales_off` — explicitly not shipped; activation is data-driven.
- Editing an existing service (rename / re-description) — v1 requires `/service_remove` + `/service_add` (kept intentionally narrow; covered in epic out-of-scope).
- Pricing commands — explicitly out of scope per the epic (prices grow via the KB-learning loop in 12.04).

## Implementation Notes
- **Endpoints follow the `app_factory` pattern** in `services/api/app/main.py` (sub-router under `/sales`). Pydantic request/response models; service-token Bearer auth via the existing dependency.
- **Commands follow the Epic-09 dispatch pattern** in `services/bot_gateway/app/` (`_SLASH_RE`-style regex; gate on operator/admin **before** calling the api). Resolve `project_id` from the operator's mapping (Epic 10) — never trust a chat_id-as-project_id shortcut.
- All command handlers are `async def` and call the api via the existing `ApiClient` (Bearer `internal_service_token`). Failure to reach the api → DM the operator a one-line error and log `sales_command_api_error` with `trace_id`.
- The `|` pipe separator MUST be a literal in the user-facing message — not the regex-special `\|`. Split with `text.split("|", 1)` and `strip()` both halves.
- Echo format for `/sales_state` MUST omit any `telegram_file_id`, refresh token, API key, or operator chat_id from the proposal payload — the existing log-capture test covers this seam.
- The `unauthorized_sales_command` log event is `snake_case verb_noun` per the project-context convention and always includes `trace_id` + `from_username`.

## Test Plan
### Unit
- `tests/test_bot_gateway_sales_commands.py` — `parse_service_add` parses name-only, name+description, rejects empty name, rejects empty description after `|`, handles multiple `|` (only the first splits); `parse_service_remove` rejects non-int / negative / zero; `parse_sales_state` extracts `@username` or returns `None`.
- `tests/test_api_sales_services_endpoint.py` — `POST` happy path returns id; `POST` duplicate `(project_id, name)` returns 409; `GET` returns active rows only; `DELETE` flips `is_active`; missing service-token returns 401.
- `tests/test_api_sales_state_endpoint.py` — `GET ?project_id=` returns active states; `GET ?project_id=&chat_id=` filters; empty list returned cleanly.

### Integration
- `tests/test_bot_gateway_sales_command_dispatch.py` — full slash command → gate check → `ApiClient` call → Telegram echo string. Covers: authorized operator (success), authorized admin (success), unauthorized sender (ignored with log assertion), api 409 → operator gets one-line "already exists" message.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_service_commands.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-02")`):
  - operator DMs `/service_add Медовеевка Лайт | Лайт уровень, с видами` → api row exists, bot echoes `Добавлено: Медовеевка Лайт (id=1)`.
  - operator DMs `/service_list` → bot echoes the single row.
  - operator DMs `/service_remove 1` → row marked inactive, `/service_list` now returns the empty-state hint.
- Telegram is stubbed at the module-level `TelegramBotSender.send` (existing pattern).

## Manual Verification
1. Start the stack; DM the bot from the operator account: `/service_add Медовеевка Лайт | Лайт уровень, с видами` → expect `Добавлено: Медовеевка Лайт (id=1)`.
2. `/service_list` → expect one row.
3. `/sales_state` (no chats yet) → `Активных бесед нет.`
4. Try `/service_add` as a non-operator account → no reply, log shows `unauthorized_sales_command`.

## Done Criteria
- 100% coverage on `services/bot_gateway/app/sales_commands.py`, the new api endpoint handlers, and the slash-command dispatch glue.
- `ruff check .` passes; E2E green.
- Gating semantics match Epic 09 (operator OR admin only; unauthorized ignored + logged).
- No raw SQL outside the repos from 12.01; all DB access flows through `ServicesRepository` / `StateRepository`.
- One-line usage examples returned on every validation error.
