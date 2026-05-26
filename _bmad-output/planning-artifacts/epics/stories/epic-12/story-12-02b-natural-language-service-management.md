# Story 12.02b — Natural-language operator dialog for service management

## Objective
Let the operator manage the `services` catalog from Telegram **without** memorizing slash commands. Free-text messages like `"добавь услугу Медовеевка Лайт — лайт уровень, с видами"` or `"удали услугу каньонинг"` or `"какие у нас услуги?"` resolve to the same handlers as `/service_add`, `/service_remove`, and `/service_list` from story 12.02. No new API endpoints, no new persistence — just an LLM-driven intent classifier in the bot_gateway that maps operator NL to existing operator-command intents.

## Scope

### In Scope
- New module `services/bot_gateway/app/operator_service_nl.py`:
  - `async def classify_service_intent(text: str, *, openrouter: OpenRouterClient) -> ServiceIntent | None`.
  - Returns `ServiceIntent(action: Literal["add", "remove", "list", "describe"], name: str | None, description: str | None)`.
  - Calls `OpenRouterClient.complete_json(...)` with `system_prompts/operator_service_nl.txt` and a strict JSON schema; returns `None` on schema-violation or low confidence.
  - Examples in the prompt cover:
    - `"добавь услугу Медовеевка Лайт"` → `{action: "add", name: "Медовеевка Лайт", description: null}`
    - `"добавь услугу каньонинг — спуск по верёвке"` → `{action: "add", name: "каньонинг", description: "спуск по верёвке"}`
    - `"удали услугу каньонинг"` → `{action: "remove", name: "каньонинг"}`
    - `"какие у нас услуги?"` / `"список услуг"` → `{action: "list"}`
    - `"опиши каньонинг как X"` → `{action: "describe", name: "каньонинг", description: "X"}` (sugar for remove + add; semantically an upsert in v1)
- Operator-message dispatch hook in `services/bot_gateway/app/main.py`:
  - When the inbound message comes from an authorized operator AND **does not** match an existing slash command AND **does not** match an existing `/kb_add` / sales-context NL intent (Epic 09 patterns), pass it through `classify_service_intent`.
  - On a high-confidence classification, route to the same internal handler that story 12.02 wired (`POST /sales/services`, `DELETE /sales/services/{id}`, `GET /sales/services`).
  - Echo the same confirmation messages as the slash-command path (`Добавлено: каньонинг (id=12)`, `Удалено: id=12`, `Не найдено: id=12`, the list view) — DRY: no parallel reply formatter.
  - On `action: "describe"` with an existing service: implement as a soft-delete + add (since 12.02 explicitly disallowed in-place edit in v1); reply `Обновлено: каньонинг (id=13)`.
  - On low-confidence / `None` classification → fall through to the existing pipeline (the message is NOT a service-management intent; it might be a customer-shaped message from the operator chat or something else for HITL).
- Cancel-on-collision: NL classification ONLY fires when the inbound text doesn't already match a registered slash command. The slash-command regex check runs first; NL is the fallback.

### Out of Scope
- Editing arbitrary fields beyond `name`/`description` (no NL tag management, no NL active/inactive toggle).
- NL management of `client_materials` — that's KB-upload-driven (12.05b) or `/material` (12.05); no NL pathway in v1.
- NL management of pricing — pricing is KB-learned per 12.04; no NL pricing commands.
- Bulk operations ("удали все услуги") — explicit per-service NL only.
- Operator language switching — Russian-first; English NL not in v1.

## Implementation Notes
- **DRY against story 12.02.** The NL handler MUST call the same internal `ApiClient.create_service(...)` / `delete_service(...)` / `list_services(...)` methods that the slash handlers call. No duplicated business logic, no duplicated validation, no duplicated reply text.
- **LLM call is JSON-structured.** Strict response schema; on schema-violation → log `operator_service_nl_schema_violation` + return `None` (fall through). Never crash, never propagate the exception out of the dispatch hook.
- **Confidence gating.** The prompt instructs the LLM to set `action: null` when uncertain (e.g., the operator said `"послушай"`). The classifier returns `None` for any `action: null` response. This avoids false-positive service-add on chatty operator messages.
- **Operator-only.** This handler is gated identically to the slash commands — only project's effective operator / admin per Epic 10. Unauthorized senders → ignored with `unauthorized_service_nl` log + no LLM call (cost-saving).
- **Cost discipline.** The classifier runs on every authorized operator inbound that didn't match a slash command. Cap the input length at 500 chars before the LLM call to avoid runaway costs on a long pasted message.
- **No persona prompt.** This is operator-facing, not customer-facing; the system prompt is utilitarian + Russian, no sales-persona voice. (Epic-10 NL admin dialog is the reference pattern.)
- **Trace metadata:** every NL-handled action logs `operator_service_nl_action_taken` with `{trace_id, action, service_id, from_username}`.

## Test Plan
### Unit
- `tests/test_operator_service_nl_classifier.py` — matrix of NL phrases against a fake LLM returning the expected structured JSON; covers add (with + without description), remove, list, describe, and the `action: null` no-classify case.
- `tests/test_operator_service_nl_schema_violation.py` — malformed LLM JSON → `None`, logged, no exception.
- `tests/test_operator_service_nl_cost_cap.py` — input > 500 chars is truncated before the LLM call (asserted via captured prompt args).

### Integration
- `tests/test_bot_gateway_nl_service_add.py` — operator DM `"добавь услугу Медовеевка Лайт"` → calls api `POST /sales/services` → echoes the same confirmation as `/service_add Медовеевка Лайт`.
- `tests/test_bot_gateway_nl_service_describe.py` — NL `"опиши каньонинг как спуск по верёвке"` against an existing service → soft-delete + add; reply `Обновлено: каньонинг (id=N)`.
- `tests/test_bot_gateway_nl_fallthrough_on_low_confidence.py` — operator DM `"привет, как дела?"` → classifier returns `None`; the message falls through the inbound pipeline unchanged.
- `tests/test_bot_gateway_nl_unauthorized_no_llm_call.py` — non-operator inbound matching a NL service phrase → no LLM call, no api call, no reply; logged.
- `tests/test_bot_gateway_nl_collides_with_slash.py` — operator DM starts with `/service_add ...` → slash handler wins; NL classifier not invoked (regression assertion via a spy on the LLM client).

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_nl_service_management.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-02b")`):
  - Authorized operator sends three NL messages in sequence: `"добавь услугу Медовеевка Лайт — лайт уровень, с видами"`, `"список услуг"`, `"удали услугу Медовеевка Лайт"`. Asserts the resulting `services` rows + the operator-facing confirmations.
- Telegram is stubbed at the existing `TelegramBotSender.send` seam.

## Manual Verification
1. As the project's effective operator, DM the bot: `"добавь услугу Медовеевка Лайт — лайт уровень, с видами"`. Expect `Добавлено: Медовеевка Лайт (id=1)`.
2. `"какие у нас услуги?"` → expect the same list view as `/service_list`.
3. `"опиши Медовеевка Лайт как тур по горным видам"` → expect `Обновлено: Медовеевка Лайт (id=2)`.
4. `"удали услугу Медовеевка Лайт"` → expect `Удалено: id=2`.
5. `"привет"` → no service-management action; falls through to whatever the bot would normally do with operator chatter.

## Done Criteria
- 100% coverage on `operator_service_nl.py` and the new bot_gateway NL-dispatch branch.
- `ruff check .` passes; E2E green.
- NL handler reuses the same `ApiClient` methods as the slash handlers (DRY assertion: handler calls a shared internal helper, not a duplicated request builder).
- Slash commands take precedence; NL only fires when no slash matches.
- Unauthorized senders never trigger an LLM call (cost-control assertion via spy).
- Operator system prompt is Russian-first, utilitarian, no persona voice.
- Schema-violation never crashes the dispatch path.
