# Story 11.01 — Calendar schema, project settings, and repositories

## Objective
Lay the data foundation for Epic 11: create `.data/semantaix_calendar.db` with idempotent schemas and the sync `*Repository` classes that own all access, with calendar **disabled for every project by default**. This story ships no customer-visible behavior — it is the substrate every later story builds on.

## Scope

### In Scope
- New SQLite DB `settings.calendar_db_path` (default `.data/semantaix_calendar.db`), bootstrapped idempotently on api startup (mirror the Epic-10 `projects`/`operators` bootstrap).
- Tables (all `CREATE TABLE IF NOT EXISTS`):
  - `calendar_project_settings(project_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, calendar_operator TEXT, project_timezone TEXT NOT NULL DEFAULT 'Europe/Moscow', lookahead_days INTEGER NOT NULL DEFAULT 60, updated_at TEXT)`.
  - `calendar_operator_tokens(project_id INTEGER, operator TEXT, refresh_token_encrypted BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'connected', created_at TEXT, updated_at TEXT, PRIMARY KEY(project_id, operator))` — `status ∈ {connected, reconnect_needed}`.
  - `calendar_oauth_pending_state(state_hash TEXT PRIMARY KEY, project_id INTEGER, operator TEXT, created_at TEXT, expires_at TEXT, consumed_at TEXT)`.
  - `calendar_service_rules(id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT, duration_minutes INTEGER, working_hours_json TEXT, service_days_json TEXT, date_exceptions_json TEXT, updated_at TEXT)`.
- `services/api/app/calendar/settings_repository.py` `CalendarSettingsRepository(*, db_path)`:
  - `get(project_id) -> CalendarProjectSettings | None`; `is_enabled(project_id) -> bool` (cheap, used by the opt-in gate).
  - `enable(project_id, *, calendar_operator, project_timezone, lookahead_days)`, `disable(project_id)`, `set_calendar_operator(...)`.
  - `list_service_rules(project_id) -> list[ServiceRule]`, `upsert_service_rule(...)`, `delete_service_rule(id)`.
- `services/api/app/calendar/token_repository.py` `CalendarTokenRepository(*, db_path, fernet)`:
  - `upsert(project_id, operator, refresh_token) -> None` (encrypts via injected `Fernet`; upsert on PK).
  - `get_refresh_token(project_id, operator) -> str` (decrypts) — raises `TokenNotFound`.
  - `set_status(project_id, operator, status)`, `delete(project_id, operator)`.
- `services/api/app/calendar/oauth_state_repository.py` `CalendarOAuthStateRepository(*, db_path)`:
  - `create(*, project_id, operator, ttl_seconds, now) -> str` (returns plaintext `state`; stores sha256 + `expires_at`).
  - `consume(state, *, now) -> PendingState` (atomic single-use: unconsumed + unexpired → mark consumed + return; else raise `InvalidOAuthState`).
- Frozen dataclasses: `CalendarProjectSettings`, `ServiceRule`, `PendingState`.
- New `Settings` fields: `calendar_db_path`, `calendar_token_encryption_key` (env), `google_oauth_client_id`, `google_oauth_client_secret`, `google_oauth_redirect_uri`, `calendar_oauth_state_ttl_seconds` (default 300). `.env.example` entries added.

### Out of Scope
- OAuth flow, token refresh, freeBusy, availability math, the answerer (later stories). Repos only.
- Any admin UI for editing settings/rules (config inserted directly / via future surface).

## Implementation Notes
- **Sync `sqlite3` repos** (project-context rule); callers dispatch via `asyncio.to_thread`. No raw SQL outside these repos.
- Encryption: `cryptography.fernet.Fernet`; the key comes from `Settings` (env) and is **injected** into `CalendarTokenRepository` — never read from the DB, never logged.
- Timestamps ISO-8601 UTC (`datetime.now(timezone.utc).isoformat()`), consistent with other repos. The `now` is **injected** into `create`/`consume` for deterministic tests.
- `state` hashing: `hashlib.sha256(state.encode()).hexdigest()`; plaintext `state` via `secrets.token_urlsafe(32)`; comparisons via lookup on hash.
- Bootstrap creates a `calendar_project_settings` row lazily (or relies on `get` returning `None` ⇒ treated as disabled). Default-off must hold even with no row.

## Test Plan
### Unit
- `tests/test_calendar_settings_repository.py` — enable/disable round-trip; `is_enabled` False when no row; service-rule upsert/list/delete; tmp `db_path`.
- `tests/test_calendar_token_repository.py` — encrypt→decrypt round-trip; `get_refresh_token` raises `TokenNotFound` on miss; upsert overwrites; `set_status`/`delete`; stored blob is not the plaintext token.
- `tests/test_calendar_oauth_state_repository.py` — `create` stores hash + TTL; `consume` succeeds once then raises on replay; raises on expired (`now` past `expires_at`); raises on unknown.

### Integration
- Idempotent bootstrap: calling the schema init twice leaves tables intact and rows preserved.

## Automated E2E verification
- None for this story (no externally observable behavior). Coverage is unit-level.

## Manual Verification
1. `docker compose up --build api` → confirm `.data/semantaix_calendar.db` exists with the four tables (`sqlite3 .data/semantaix_calendar.db ".tables"`).
2. Confirm every project reads as calendar-disabled by default.

## Done Criteria
- 100% coverage on the three new repo modules + dataclasses.
- `ruff check .` passes.
- Encryption key / refresh token never logged (log-capture assertion in the token-repo test).
- Bootstrap is idempotent and default-off.
