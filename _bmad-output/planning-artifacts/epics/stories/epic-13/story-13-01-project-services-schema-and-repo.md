# Story 13.01 — `project_services` schema, repository, and migration

## Objective
Lay the data + repository foundation for Epic 13: rename Epic 11's `calendar_service_rules` to `project_services` (in the same `.data/semantaix_calendar.db`), add the four catalog columns, enforce `UNIQUE(project_id, lower(name))`, and ship the canonical `ProjectServiceRepository` (sync `sqlite3` + per-`(project_id, lower(name))` `asyncio.Lock` single-flight upsert). Also create the `services_nl_op_sessions` table in `.data/semantaix_nl_ops.db` (consumed by story 13.04) and the new data file `data/russian_calendar_terms.json` (consumed by story 13.06's prose renderer). This story ships no customer-visible behavior — it is the substrate every later story builds on. Architecture reference: `_bmad-output/planning-artifacts/architecture.md` lines 222–252; FR reference: PRD §FR-23.

## Scope

### In Scope
- **Migration (idempotent, guarded)** in `.data/semantaix_calendar.db`:
  - Check `SELECT name FROM sqlite_master WHERE type='table' AND name IN ('calendar_service_rules','project_services')` — if `calendar_service_rules` exists and `project_services` does not → `ALTER TABLE calendar_service_rules RENAME TO project_services`; if `project_services` already exists → skip the rename.
  - For each new column (`description TEXT`, `price_text TEXT`, `tags_json TEXT`), `PRAGMA table_info(project_services)` and `ADD COLUMN` only when the column is absent.
  - **Fresh-deploy path:** if neither `calendar_service_rules` nor `project_services` exists, `CREATE TABLE project_services` directly with the final schema (no requirement that any Epic 11 migration has run first).
  - Create `UNIQUE(project_id, lower(name))` constraint and `project_services_project_idx` index (both `IF NOT EXISTS`).
  - Touches **only** the rename + four new columns + the new uniqueness/index. The other tables in `semantaix_calendar.db` (`calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state`) are unchanged.
- **Final `project_services` schema:** `id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, price_text TEXT, tags_json TEXT, duration_minutes INTEGER, working_hours_json TEXT, service_days_json TEXT, date_exceptions_json TEXT, updated_at TEXT`.
- **JSON column shapes** pinned: `working_hours_json` = `{"mon":[["10:00","19:00"]],"tue":[["10:00","13:00"],["14:00","18:00"]]}`; `service_days_json` = `["mon","tue","wed","thu","fri","sat"]`; `date_exceptions_json` = `["2026-01-01","2026-05-09"]`.
- **New `services/api/app/calendar/project_services_repository.py`** `ProjectServiceRepository(*, db_path)`:
  - `upsert(project_id, *, name, description=None, price_text=None, tags=None, duration_minutes=None, working_hours=None, service_days=None, date_exceptions=None) -> ProjectService` — keyed on `(project_id, lower(name))`; converts an existing-name insert into an UPDATE; emits `services_upsert_duplicate_name` structured log when an UPDATE was triggered.
  - `get(project_id, service_id) -> ProjectService` — raises `ProjectServiceNotFound` on miss.
  - `get_by_name(project_id, name) -> ProjectService | None` — case-insensitive (FR-24 edit/remove target resolution).
  - `list_for_project(project_id) -> list[ProjectService]` — catalog answer reads this (all rows).
  - `list_calendar_eligible(project_id) -> list[ProjectService]` — calendar reads this (filters `duration_minutes IS NOT NULL`).
  - `delete(project_id, service_id) -> None` — raises `ProjectServiceNotFound` on miss.
- **Per-`(project_id, lower(name))` `asyncio.Lock`** (single-flight) — module-level dict of locks, created lazily; mirrors Epic 11's per-operator token-refresh lock pattern. The lock is acquired by the **caller** (the api endpoint / NL ops handler) before invoking `to_thread(repo.upsert)`, so the sync repo stays pure-sqlite3. Helper `acquire_service_upsert_lock(project_id, name) -> asyncio.Lock` lives in the repo module.
- **Delegating aliases on `CalendarSettingsRepository`** (Epic 11 module): `upsert_service_rule`, `list_service_rules`, `delete_service_rule` keep their existing signatures but internally instantiate a `ProjectServiceRepository` against the same `db_path` and delegate. Each call emits `deprecation_warning_calendar_settings_service_rule` (logger name + event key only; no payload values). Aliases pinned for removal in **Epic 13 cleanup PR (≤60 days post-merge)**.
- **Frozen dataclass `ProjectService`** + exception `ProjectServiceNotFound`. Optional `working_hours`/`service_days`/`date_exceptions` are deserialized to dict/list (or `None` if column NULL) so callers never touch raw JSON strings.
- **New table `services_nl_op_sessions`** in `.data/semantaix_nl_ops.db` (created during this story so 13.04 can consume it; the bootstrap call is made from the api startup hook alongside the existing `admin_nl_op_sessions` bootstrap). Columns mirror `admin_nl_op_sessions`: `id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, originating_operator TEXT NOT NULL, op_type TEXT NOT NULL, payload_json TEXT NOT NULL, confirm_token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_confirmation', preview TEXT, created_at TEXT, expires_at TEXT, consumed_at TEXT, cancelled_at TEXT`. Indexes on `(project_id, originating_operator, status)` and `(status, expires_at)` (lazy expiry reap). **The session state machine + repository class itself lands in story 13.04**; this story only creates the table.
- **New data file `data/russian_calendar_terms.json`** — day-code map (`{"mon":{"short":"пн","full":"понедельник"}, …}`), month-name table for date-exception rendering (`"2026-01-01"` → `"1 января"` via month-genitive lookup `{1:"января", 2:"февраля", …}`), and short labels for closure phrasing (`"closed_prefix":"закрыто:"`). Owned by this story because the schema migration runs first; consumed by `services_render` in story 13.06.

### Out of Scope
- The `ServicesNlOpsRepository` class + `parse_service_intent` parser (story 13.04 owns the session repository + regex parser; this story only creates the empty table).
- The api endpoints (story 13.02), slash command (13.03), NL dialog (13.05), prose renderer (13.06), catalog-answerer cutover (13.06).
- Any change to `calendar_project_settings`, `calendar_operator_tokens`, or `calendar_oauth_pending_state`.

## Implementation Notes
- **Sync `sqlite3` repo + caller-acquired `asyncio.Lock`** — repo stays pure-sqlite3 (per project-context rule); the lock lives in the same module so callers acquire it before dispatching to `asyncio.to_thread`. Use a module-level `dict[(int,str), asyncio.Lock]` plus a module-level `asyncio.Lock` to guard lazy creation of the per-key locks.
- **`(project_id, lower(name))` lock key** — match the uniqueness constraint exactly; `name.casefold()` (Russian-safe) when building the key.
- **Migration idempotency** — `PRAGMA table_info(project_services)` returns column metadata; build a `set(row["name"] for row in cursor)` and only `ADD COLUMN` when absent. The rename step uses `sqlite_master`'s `name` column, not raw `try/except` on `ALTER`.
- **`upsert` IntegrityError handling** — even with the lock, an upsert can race against an alias-path write (the deprecated `CalendarSettingsRepository.upsert_service_rule`). Catch `sqlite3.IntegrityError` on UNIQUE violation → re-do as UPDATE in the same transaction (`INSERT … ON CONFLICT(project_id, lower(name)) DO UPDATE SET …`-equivalent expressed as Python branching since SQLite UNIQUE on `lower(name)` cannot be used directly in `ON CONFLICT` — implement as SELECT-then-INSERT-or-UPDATE inside `BEGIN IMMEDIATE`).
- **`secrets` / `hmac` / `Fernet` not used here** — those land in 13.04 (session tokens). This story only does schema + CRUD.
- **Bootstrap call** — extend the api startup hook (next to the existing `CalendarSettingsRepository` bootstrap) to run the `project_services` migration AND the `services_nl_op_sessions` `CREATE TABLE IF NOT EXISTS`. Both are idempotent and safe to re-run on every container boot.
- **Russian rendering data file** — `data/russian_calendar_terms.json` is loaded ONCE at process startup (lazy module-level cache) by the renderer in 13.06; this story just creates the file with day-code + month-genitive + closed-prefix entries. No code in this story reads it.
- **Logging hygiene** — `services_upsert_duplicate_name` log carries `project_id` + `service_id` + `name` (operator content is non-secret per FR-24 audit posture); `deprecation_warning_calendar_settings_service_rule` carries only the event key + `project_id` (no payload values — keeps the deprecation log low-noise).

## Test Plan

### Unit
- `tests/test_project_services_repository.py`:
  - `upsert` insert path → row exists; `upsert` second call with same `(project_id, lower(name))` → UPDATE (same `id`); `services_upsert_duplicate_name` log emitted on the UPDATE path.
  - `upsert` with mixed-case names (`Маникюр` vs `маникюр`) → second call updates the first row.
  - `get_by_name` case-insensitive hit + miss → returns `None`; `get(service_id)` → returns row / raises `ProjectServiceNotFound`.
  - `list_for_project` returns all rows; `list_calendar_eligible` filters `duration_minutes IS NULL` rows out.
  - `delete` removes; second `delete` raises `ProjectServiceNotFound`.
  - JSON round-trip: `working_hours_json` `{"mon":[["10:00","19:00"]]}` survives insert→read; multi-window `{"tue":[["10:00","13:00"],["14:00","18:00"]]}` survives; `date_exceptions_json` `["2026-01-01"]` survives.
  - `acquire_service_upsert_lock(project_id, name)` returns the SAME `asyncio.Lock` for `(1,"Маникюр")` and `(1,"маникюр")` and a DIFFERENT one for `(2,"маникюр")`.
- `tests/test_project_services_migration.py`:
  - **Idempotency:** create a fresh DB, run the migration twice → second run is a no-op (no `duplicate column name`, no `no such table`); column set unchanged; row count unchanged.
  - **Rename path:** seed a DB with `calendar_service_rules` (old Epic-11 schema) and a row → run migration → `calendar_service_rules` no longer exists; `project_services` exists; the row is preserved; new columns are NULL on the migrated row.
  - **Fresh-deploy path:** start with no calendar tables at all → run migration → `project_services` exists with the FULL final schema; no `calendar_service_rules` table is created; the other three calendar tables are NOT created by this migration (they remain Epic 11's responsibility).
  - **Touch isolation:** seed `calendar_project_settings` / `calendar_operator_tokens` / `calendar_oauth_pending_state` rows → run migration → their schemas + row counts are unchanged (snapshot before/after).
- `tests/test_calendar_settings_service_rule_aliases.py`:
  - `CalendarSettingsRepository.upsert_service_rule(...)` writes a row visible via `ProjectServiceRepository.get`; `deprecation_warning_calendar_settings_service_rule` log emitted.
  - `list_service_rules` / `delete_service_rule` delegate similarly + emit deprecation log.
- `tests/test_services_nl_op_sessions_bootstrap.py`:
  - After api startup bootstrap, `services_nl_op_sessions` table exists in `semantaix_nl_ops.db` with the expected columns + indexes; running bootstrap twice is a no-op.
- `tests/test_russian_calendar_terms_data.py`:
  - `data/russian_calendar_terms.json` loads as valid JSON; contains keys `mon..sun` (each with `short` + `full`), `months` (1..12 genitive), `closed_prefix`.

### Contract
- (No api endpoints in this story; contract tests land in 13.02.)

### Integration
- `tests/test_api_startup_bootstrap_epic13.py` — boot the api with a fresh `.data/`; assert `semantaix_calendar.db` contains `project_services` with the final schema + `semantaix_nl_ops.db` contains `services_nl_op_sessions`.

## Automated E2E verification
- None for this story (no externally observable behavior). Coverage is unit + integration only.

## Manual Verification
1. `docker compose up --build api` against a fresh `.data/` → confirm `sqlite3 .data/semantaix_calendar.db ".schema project_services"` shows all final columns + the UNIQUE constraint and index.
2. `sqlite3 .data/semantaix_nl_ops.db ".tables"` → confirm `services_nl_op_sessions` present alongside `admin_nl_op_sessions`.
3. Stop + restart the api container → confirm no `duplicate column name` / `no such table` errors in startup logs.
4. Pre-seeded Epic-11 DB (with `calendar_service_rules` rows) → start api → confirm rows are preserved under the renamed `project_services` table; new columns are NULL on those rows.

## Done Criteria
- 100% line coverage on `services/api/app/calendar/project_services_repository.py` + the new exception/dataclass + the migration helper + the `CalendarSettingsRepository` delegating aliases.
- `ruff check .` passes.
- Bootstrap is idempotent in all three modes (fresh / migrated-from-Epic-11 / re-run).
- Other tables in `semantaix_calendar.db` are untouched by this migration (verified by snapshot test).
- `services_nl_op_sessions` table created in `semantaix_nl_ops.db` (consumed by 13.04).
- `data/russian_calendar_terms.json` exists and parses (consumed by 13.06).
- `deprecation_warning_calendar_settings_service_rule` emitted on every alias-path call.
