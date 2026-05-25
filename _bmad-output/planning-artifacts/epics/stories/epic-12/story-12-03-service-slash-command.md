# Story 12.03 — `/service` slash command + `/calendar_service` migration alias

## Objective
Ship the operator-facing slash command surface from FR-24 Path A: `/service add|edit|remove|list <name> [key=value …]` in `services/bot_gateway/app/calendar_commands.py`, **start-of-message anchored**, **operator-gated via the Epic 10 registry**. Add `/calendar_service` as a deprecation-logged alias that DMs the operator a one-time migration hint on first invocation. Both commands ride on the canonical api endpoints from story 12.02. Architecture reference: lines 235, 246; FR reference: FR-24 (Path A).

## Scope

### In Scope
- **New dispatcher** `handle_service_command(update, *, sender_username, project_id, deps)` in `services/bot_gateway/app/calendar_commands.py` (extends the existing Epic 11 calendar command module).
- **Trigger regex** `^\s*/service\b\s+(add|edit|remove|list)\b(.*)$` — start-of-message anchored; subcommand required; trailing args parsed by a `key=value` lexer (`duration=60 days=mon-sat hours=10:00-19:00 price="от 2000" desc="..." tags=classic,manicure`).
- **Subcommands:**
  - `/service add <name> [keys]` → `ApiClient.upsert_project_service(...)` against `POST /api/projects/{id}/services` (12.02). Bot DMs Russian confirmation: `"Услуга «{name}» сохранена."`.
  - `/service edit <name> [keys]` → same upsert path (semantically equivalent — `(project_id, lower(name))` uniqueness makes add/edit identical at the repo level); DMs `"Услуга «{name}» обновлена."`. Pre-resolution: if name does not match an existing row, bot DMs `"услуга «{name}» не найдена."` and returns without calling the api (uses `GET /api/projects/{id}/services` for the resolution check OR relies on a `GET .../services/{name}` shortcut if added — implementation defers to `GET .../services` + client-side lookup to keep the api surface minimal).
  - `/service remove <name>` → resolve name → `ApiClient.delete_project_service(...)` → DMs `"Услуга «{name}» удалена."`. Admin-attempts-remove returns 403 from the api (12.02); bot relays as `"Удаление услуги доступно только оператору, не администратору."`.
  - `/service list` → `GET /api/projects/{id}/services` → DMs a plain-text Russian list of names (one per line, no field labels): `"Услуги проекта: маникюр, педикюр, стрижка."`.
- **`key=value` parser:**
  - Keys: `duration` (integer minutes; reject non-digit), `days` (`mon-sat` or `mon,wed,fri` → list of 3-letter codes; ё/е + Cyrillic-dash variants normalized), `hours` (`10:00-19:00` single window or `10:00-13:00,14:00-19:00` multi-window per day), `price` (free text), `desc` (free text; quoted with `"..."` for spaces), `tags` (comma list).
  - Quoted values support `"..."` and `'...'`; embedded spaces preserved; truncated at 200 chars (consistent with NL preview cap from 12.05).
  - Unknown keys → reject with DM `"Неизвестный ключ: {key}. Допустимые: duration, days, hours, price, desc, tags."`.
- **Operator gating** — sender_username must be a registered operator on `project_id` (Epic 10 `operators` repository, via `operator_resolver`). Non-registered → ignored with logged `unauthorized_services` and **no DM** (matches NL dialog rule from 12.05).
- **Admin-attempts-`/service`** — if sender is admin AND NOT a registered project operator → ignored with logged `unauthorized_services`; if admin IS registered project operator → allowed for `add`/`edit`/`list`, rejected at the api for `remove` (relayed as the 403 message above).
- **`/calendar_service` alias** — same regex pattern as `/service` (`^\s*/calendar_service\b\s+(add|remove)\b(.*)$`; keep the Epic-11 vintage subcommand list). On every invocation:
  - Emit `deprecation_warning_calendar_service_command` log with `{operator, project_id}`.
  - **First invocation per operator** (persisted via a tiny `calendar_service_alias_hint_sent` table in `semantaix_nl_ops.db`, keyed `(project_id, operator)` — created lazily on first hit): DM the migration hint `"Команда /calendar_service устарела — используйте /service или просто напишите «добавь услугу …»."` Subsequent calls only log; no further DM. The persisted dedup means the hint survives bot restarts.
  - Delegate to `handle_service_command` after the hint logic so the user's intended action still completes.
- **R1 refinement (post-12.04):** `/service add <name>` (name-only) is valid and creates a catalog-only entry; the success DM includes a tip for adding scheduling later.

### Out of Scope
- The `/service` regex parser's ambiguity-fail-closed cases for NL dialog (12.04 / 12.05 own NL).
- The full kb_intent module (untouched).
- The api endpoints themselves (12.02 owns the routes; this story is purely the bot dispatcher).

## Implementation Notes
- **Start-of-message anchoring** — regex MUST start with `^\s*` so an operator quoting another message (`> /service add ...`) does NOT trigger; mirrors Epic 11 `/connect_calendar` anchoring.
- **`key=value` lexer** — small standalone function `parse_service_kv(args_str) -> dict`; written + tested in isolation so corner cases (quoted values, embedded `=`, escaped quotes) are covered without hitting Telegram fakes.
- **Day-range expansion** — `mon-sat` → `["mon","tue","wed","thu","fri","sat"]`; same lookup table that lives in `data/russian_calendar_terms.json` (story 12.01 owns the file; this story can import the day-code list from the same module that the renderer in 12.06 will use, or duplicate the 7-entry list inline — recommend extract a small `services/api/app/services_kv_parser.py` helper so the data file stays renderer-only and the parser stays bot-side).
- **No `MarkdownV2` / HTML** — all confirmation DMs are plain text (matches the NL preview rule from 12.05; prevents preview/echo injection if an operator embeds Telegram-format reserved chars in a service name or description).
- **One-time hint dedup** — `calendar_service_alias_hint_sent(project_id INTEGER, operator TEXT, sent_at TEXT, PRIMARY KEY(project_id, operator))` in `semantaix_nl_ops.db`. `IF NOT EXISTS` bootstrap; `INSERT OR IGNORE` on the hint path. Owned by this story (it's a behavior-attached table, not a session-state-machine table).
- **`/service list` rendering** — names only (no description / price); this matches FR-25's "general 'какие услуги?' returns names only" rule and gives the operator the same minimal view the customer would see.
- **Reuse Epic-10 admin authorization** — for the admin-who-is-registered-operator path, call the existing helper rather than re-implementing the lookup.

## Test Plan

### Unit
- `tests/test_services_kv_parser.py`:
  - `duration=60 days=mon-sat hours=10:00-19:00 price="от 2000" desc="классический и аппаратный" tags=classic,manicure` → exact extraction; quoted values preserved; spaces inside `desc` preserved.
  - `days=mon,wed,fri` → list of 3 codes.
  - Multi-window `hours=10:00-13:00,14:00-19:00` → list of 2 windows.
  - Cyrillic-dash variants `days=пн–сб` (en-dash) / `пн-сб` / `пн—сб` (em-dash) all normalize to `["mon","tue","wed","thu","fri","sat"]`.
  - `duration=полтора` → raises `InvalidKvValue("duration", ...)`.
  - Unknown key `foo=bar` → raises `UnknownKvKey("foo")`.
  - Value truncated at 200 chars; truncation marker `…` appended.
- `tests/test_handle_service_command_trigger.py`:
  - `^/service add ...` matches; `что-то /service add ...` does NOT match (start-of-message-anchored).
  - Quoted-reply prefix `> /service add ...` does NOT match.
  - Subcommand-less `/service` → DMs help; does not call api.

### Contract
- `tests/test_handle_service_command_routing.py` (with fake `ApiClient` + fake `TelegramBotSender`):
  - Operator `/service add маникюр duration=60 days=mon-sat hours=10:00-19:00 price="от 2000" desc="..."` → `ApiClient.upsert_project_service` called once with the parsed payload; DM `"Услуга «маникюр» сохранена."`.
  - Operator `/service edit маникюр price="от 2500"` → resolves existing row first; upsert called; DM updated.
  - Operator `/service edit unknown_name` → DMs `"услуга «unknown_name» не найдена."`; api NOT called.
  - Operator `/service remove маникюр` → `ApiClient.delete_project_service` called; DM `"Услуга «маникюр» удалена."`.
  - Admin-NOT-registered `/service add ...` → ignored; `unauthorized_services` log; no DM.
  - Admin-IS-registered `/service add ...` → succeeds; `/service remove ...` → api returns 403, bot DMs `"Удаление услуги доступно только оператору, не администратору."`.
  - Non-registered sender `/service add ...` → ignored; no DM; log carries `{sender, project_id, reason:"unauthorized_services"}`.
  - `/service list` → DMs names only (no prices/descriptions).
- `tests/test_calendar_service_alias.py`:
  - First operator invocation → `deprecation_warning_calendar_service_command` log + migration-hint DM + action completes; row in `calendar_service_alias_hint_sent`.
  - Second invocation (same operator + project) → log only; NO migration-hint DM (dedup hit); action completes.
  - Different project (same operator) → migration-hint DM sent (dedup is per `(project_id, operator)`).

### Integration
- Fake `ApiClient` returning realistic responses (12.02 endpoint shapes); fake `TelegramBotSender` capturing DMs.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_slash_command.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-03")`): boot api + bot_gateway against a fresh `.data/`; operator sends `/service add маникюр duration=60 days=mon-sat hours=10:00-19:00 price="от 2000"`; DB has the row; operator sends `/service list` → DM contains `маникюр`; admin (NOT registered) sends `/service add педикюр ...` → ignored, no DM; admin (registered) sends `/service add педикюр ...` → succeeds; admin sends `/service remove маникюр` → 403 relay DM; operator sends `/service remove маникюр` → success DM; final `/service list` shows only `педикюр`. Plus a `/calendar_service add ...` invocation that confirms the migration-hint DM is sent once and never again.

## Manual Verification
1. As operator in Telegram: `/service add маникюр duration=60 days=mon-sat hours=10:00-19:00 price="от 2000" desc="классический и аппаратный"` → bot DMs `"Услуга «маникюр» сохранена."`.
2. `/service list` → DMs names list including `маникюр`.
3. `/calendar_service add педикюр duration=90 ...` → bot DMs the migration hint THEN the success confirmation; repeat → only the success confirmation.
4. As admin (NOT registered operator): `/service add тест ...` → no response, log shows `unauthorized_services`.
5. As admin (registered operator): `/service add тест ...` → success; `/service remove тест` → DM `"Удаление услуги доступно только оператору, не администратору."`.

## Done Criteria
- 100% coverage on `services/bot_gateway/app/calendar_commands.py` `handle_service_command` + `parse_service_kv` + `/calendar_service` alias path + `calendar_service_alias_hint_sent` dedup.
- `ruff check .` passes.
- Start-of-message anchoring verified by a negative test (mid-message and quoted-reply both fail to trigger).
- Operator-gating enforced; non-registered → no DM.
- `/calendar_service` deprecation log on every call + migration-hint DM only on first call per `(project_id, operator)`.
- Plain-text DMs (no Markdown / HTML) verified by capture test.
