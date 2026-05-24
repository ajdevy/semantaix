# Story 12.05 — `services_nl_dialog` bot dispatcher (plain-text preview + ownership)

## Objective
Ship the bot-side half of FR-24 Path B: `services/bot_gateway/app/services_nl_dialog.py` with `handle_services_nl_message(update, *, sender_username, project_id, deps)` that DMs a plain-text Russian preview on propose, routes `да` / `/confirm <token>` and `нет` / `/cancel` to the api endpoints from story 12.04, and enforces operator-gating + ownership before any DM is sent. Mirrors `services/bot_gateway/app/admin_nl_dialog.py`. Architecture reference: lines 236, 247–248; FR reference: FR-24 Path B (preview-rendering & threat-model rules).

## Scope

### In Scope
- **New module `services/bot_gateway/app/services_nl_dialog.py`** with `handle_services_nl_message(update, *, sender_username, project_id, deps)`:
  - **Trigger:** start-of-message-anchored keyword regex `^\s*(добавь|добавьте|новая|создай|удали|измени)\s+услугу\b` (mirrors `parse_service_intent` from 12.04; defense-in-depth — the api also anchors). Anchoring prevents quote-reply triggers (`> добавь услугу ...`).
  - **Operator gating:** sender must be a registered operator on `project_id` (Epic 10 `operators` repository via `operator_resolver`). Non-registered → ignored with logged `unauthorized_services` and **no DM** (avoids customer-thread leakage if a non-operator types the trigger phrase in a customer conversation).
  - **Admin handling:** admin-AND-registered-project-operator → allowed (the api enforces add/edit vs remove at confirm time per 12.04); admin-NOT-registered → ignored same as non-registered.
  - **Propose path:** call `ApiClient.services_nl_propose(project_id, originating_operator=sender_username, raw_text=text)` → on `OP_UNKNOWN` DM the Russian clarification reason (`"не понял, добавьте по одной услуге за раз"` / `"укажите длительность числом в минутах"` / generic `"не понял, уточните"`); on success render a plain-text preview DM:
    - **Preview shape (plain text, no MarkdownV2 / no HTML parse_mode):** `"Создать услугу «{name}» (60 мин, пн–сб 10:00–19:00, цена от 2000 ₽, описание: …). Подтвердите ответом «да» или /confirm {token}. Отмена: «нет» или /cancel."` for `op_type=add`; analogous wording for `edit` (`"Изменить услугу «…»"`) and `remove` (`"Удалить услугу «…»"`).
    - **Operator-supplied content escaping + 200-char cap:** `name`, `description`, `price_text`, `tags` are each length-capped at 200 chars with a trailing `…` on truncation; control characters stripped (`\x00..\x1f`, `\x7f`); Telegram-format reserved chars (`*`, `_`, `[`, `]`, `(`, `)`, `~`, `\``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!`) NOT escaped (because we send `parse_mode=None`, but verified by a "no parse_mode" assertion in the test).
- **Confirm routing:** when sender DMs `да` or `/confirm <token>` (with `<token>` matching the regex `[A-Za-z0-9_-]{22}` for `secrets.token_urlsafe(16)`):
  - For `да` (no explicit token): call `ApiClient.services_nl_latest_pending(project_id, originating_operator=sender_username)`; if a pending row exists, use its `confirm_token` (the token came from the same operator's preview DM — fetching latest-pending is the convenience-shortcut; equivalent to copying the token).
  - For `/confirm <token>`: extract the token directly.
  - Call `ApiClient.services_nl_confirm(project_id, session_id, presented_token, presenter_operator=sender_username)`. On 200 → DM `"Операция применена: {op_type}"` (per FR-24-style copy). On 403 `not_session_owner` → DM `"Сессия не принадлежит вам."`. On 410 `session_expired` → DM `"Сессия истекла, начните заново."`. On 401 `invalid_token` → DM `"Неверный токен."`. On 410 `session_not_pending` → DM `"Сессия уже применена или отменена."`. On 403 `admin_cannot_remove_service` → DM `"Удаление услуги доступно только оператору, не администратору."`.
- **Cancel routing:** `нет` or `/cancel` (latest-pending lookup analogous to `да`). Call `ApiClient.services_nl_cancel(project_id, session_id, presenter_operator=sender_username)`. On 200 → DM `"Запрос отменён."`. On 403 / 410 → equivalent Russian copy.
- **Prior-pending cancellation notice:** when the api's `POST .../services/nl-ops` response indicates that a prior session was cancelled (the api returns `prior_cancelled_session_id` in the response when applicable per 12.04 — add this small field to the response if not already there), bot DMs the additional line `"Ваш предыдущий запрос отменён, заменён новым."` BEFORE the new preview.
- **`ApiClient` additions** in `services/bot_gateway/app/api_client.py`: `services_nl_propose`, `services_nl_confirm`, `services_nl_cancel`, `services_nl_latest_pending` — thin httpx wrappers around the 12.04 endpoints, all using `internal_service_token`.

### Out of Scope
- The api endpoints + parser + repository (12.04).
- The slash command + `/calendar_service` alias (12.03).
- Web admin UI (deferred — not part of Epic 12).

## Implementation Notes
- **Mirror `services/bot_gateway/app/admin_nl_dialog.py`** — same dispatcher shape (read its handler signature + return value before writing). The differences to call out clearly:
  1. project-scoped (not global like admin nl ops);
  2. operator-gated (Epic 10 registry) rather than admin-username-gated;
  3. plain-text preview (no MarkdownV2 / HTML) — admin dialog may use Markdown today but the operator-content threat model demands plain text here per FR-24 / decision-log H4 + H5.
- **`parse_mode=None`** — every `TelegramBotSender.send_message` call in this module passes `parse_mode=None` (or simply omits it if the sender defaults to None). A unit test asserts the kwarg is `None` on the captured calls — prevents a future refactor from silently flipping to Markdown.
- **200-char cap + ellipsis** — a tiny helper `escape_and_cap(value, *, max_len=200)` returns `(value or "")[:max_len] + ("…" if len(value or "") > max_len else "")` plus control-char stripping; lives in `services_nl_dialog.py` (single-purpose, story-local).
- **Token regex for `/confirm <token>`** — `re.compile(r"^/confirm\s+([A-Za-z0-9_-]{22})\s*$")`. `secrets.token_urlsafe(16)` yields 22-char URL-safe base64; the regex is tight to reject typos / pasted noise (the api will also reject, but a tight regex saves a network round trip).
- **`да` / `нет` matching** — case-insensitive, exact match after strip (`text.strip().lower() in {"да","нет"}`); subsequence matches (`"да, давай"`) do NOT trigger — they fall through to other handlers / are ignored. This is a deliberate UX tradeoff: explicit `да` is the contract.
- **Ownership re-check on the bot side** — when fetching latest-pending for `да`, the api scopes by `originating_operator=sender_username` so it cannot return another operator's session. The api's confirm endpoint applies the authoritative ownership check; the bot is defense-in-depth.
- **No DM on operator-gating failure** — verified by a negative test (non-registered sender → zero `send_message` calls). This is explicit because the previous Epic 11 calendar commands DO DM `unauthorized_calendar` on some paths — Epic 12 NL dispatch breaks from that pattern per FR-24 decision-log L5 to avoid leakage into customer threads.

## Test Plan

### Unit
- `tests/test_services_nl_dialog_trigger.py`:
  - `^\s*(добавь|добавьте|новая|создай|удали|измени)\s+услугу\b` regex matches all 6 verbs + `услугу`; does NOT match mid-message (`пожалуйста добавь услугу ...` fails); does NOT match quoted reply (`> добавь услугу ...` fails).
- `tests/test_services_nl_dialog_routing.py` (with fake `ApiClient` + fake `TelegramBotSender`):
  - **Propose (operator, parser-OK):** api returns `{session_id, preview, confirm_token, expires_at}` → exactly one DM sent with `parse_mode=None`, preview text matches the FR-24 format, contains the token, contains `/cancel` instruction.
  - **Propose (operator, parser-OP_UNKNOWN):** api returns `{op_type:"OP_UNKNOWN", reason}` → DM with the mapped Russian reason (`"не понял, добавьте по одной услуге за раз"` etc.).
  - **Propose (operator, prior pending cancelled):** api response includes `prior_cancelled_session_id` → bot DMs the cancellation notice BEFORE the new preview (2 DMs in order).
  - **Confirm via `да`:** `latest_pending` returns the session → `services_nl_confirm` called with `presenter_operator=sender_username` → DM `"Операция применена: add"`.
  - **Confirm via `/confirm <token>`:** token extracted by tight regex → confirm called.
  - **Confirm 403 not_session_owner:** DM `"Сессия не принадлежит вам."`.
  - **Confirm 410 expired:** DM `"Сессия истекла, начните заново."`.
  - **Confirm 401 invalid_token:** DM `"Неверный токен."`.
  - **Confirm 410 session_not_pending:** DM `"Сессия уже применена или отменена."`.
  - **Confirm 403 admin_cannot_remove_service:** DM the operator-only-remove message.
  - **Cancel via `нет` / `/cancel`:** parallel happy + error paths.
  - **Non-registered sender:** trigger matches → zero `send_message` calls; `unauthorized_services` log emitted.
  - **Admin-NOT-registered sender:** same as non-registered (zero DMs, log).
  - **Admin-registered sender:** propose succeeds; confirm on `op_type=remove` returns 403 → DM the operator-only-remove message.
  - **`parse_mode=None` assertion:** every captured `send_message` call has `parse_mode=None`.
  - **Operator-text length cap:** when api preview contains a 500-char `description`, the bot does NOT re-cap (the api/preview was already capped); but on the OP_UNKNOWN error-mapping path, any raw operator text echoed back is run through `escape_and_cap(..., max_len=200)`.

### Contract
- (None new at HTTP layer — bot consumes the 12.04 api contract which already has its own contract tests.)

### Integration
- `tests/test_services_nl_dialog_integration.py` — full propose→da→confirm flow against an in-process api stub (`pytest-httpx` style) + fake `TelegramBotSender`; assert the final upsert happened and the operator received exactly 2 DMs (preview, then success).

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_nl_dialog_round_trip.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-05")`): boot api + bot_gateway against a fresh `.data/`; operator A DMs `"добавь услугу педикюр на 90 минут, вт–сб, 10:00–18:00, цена 2500"` → captured DMs: preview with token; operator A replies `да` → DM `"Операция применена: add"` + row exists. Then: operator A starts a second propose while a fresh one is pending → DM order: cancellation-notice → new preview. Then: operator B presents operator A's token via `/confirm <token>` → DM `"Сессия не принадлежит вам."` Then: customer-thread sender (NOT a registered operator) types `добавь услугу X ...` → zero DMs, `unauthorized_services` log captured.

## Manual Verification
1. As operator in Telegram: `добавь услугу маникюр на 60 минут, пн–сб, 10–19, цена 2000, описание: классический` → bot DMs a plain-text preview with a token and a `/cancel` instruction; reply `да` → bot DMs `"Операция применена: add"`; `GET /api/projects/1/services` shows the row.
2. Try `добавь услугу маникюр и педикюр` → bot DMs `"не понял, добавьте по одной услуге за раз"`.
3. Start a second propose while one is pending → bot DMs `"Ваш предыдущий запрос отменён, заменён новым."` then the new preview.
4. Have a second operator (operator B) DM `/confirm <token-from-operator-A>` → bot DMs `"Сессия не принадлежит вам."`.
5. As a non-registered user, type `добавь услугу X` → no DM; api logs `unauthorized_services`.

## Done Criteria
- 100% coverage on `services/bot_gateway/app/services_nl_dialog.py` (dispatcher + helpers) and the new `ApiClient` methods.
- `ruff check .` passes.
- Start-of-message anchoring verified (mid-message + quoted-reply fail to trigger).
- Operator-gating enforced; non-registered → zero DMs.
- All DMs sent with `parse_mode=None` (asserted by capture test).
- Operator-supplied content length-capped at 200 chars; control chars stripped.
- Cross-operator replay shows the `not_session_owner` Russian message.
- Prior-pending cancellation triggers the explicit "previous request cancelled" notice before the new preview.
