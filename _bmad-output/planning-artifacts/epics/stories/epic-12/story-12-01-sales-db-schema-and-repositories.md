# Story 12.01 — Sales DB schema, repositories, and bootstrap

## Objective
Lay the data foundation for Epic 12: create `.data/semantaix_sales.db` with idempotent schemas and the sync `*Repository` classes that own all access, with **no `services` rows by default** (which keeps the answerer dormant per the data-driven activation rule). This story ships no customer-visible behavior — it is the substrate every later story builds on.

## Scope

### In Scope
- New SQLite DB `settings.sales_db_path` (default `.data/semantaix_sales.db`), bootstrapped idempotently on api startup (mirror the Epic-10 `projects`/`operators` bootstrap).
- Tables (all `CREATE TABLE IF NOT EXISTS`):
  - `sales_conversation_state(chat_id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, current_stage TEXT NOT NULL DEFAULT 'new', collected_intent_json TEXT NOT NULL DEFAULT '{}', last_proposal_json TEXT, last_customer_msg_at TEXT, last_bot_msg_at TEXT, updated_at TEXT NOT NULL)` — `current_stage ∈ {new, scoping, pitching, pricing, proposing, closing, dormant}`.
  - `services(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, name TEXT NOT NULL, description_md TEXT, tags_json TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, name))`.
  - `client_materials(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, kind TEXT NOT NULL, telegram_file_id TEXT, local_path TEXT NOT NULL, byte_size INTEGER NOT NULL, duration_seconds INTEGER, caption TEXT, tags_json TEXT, source_operator_file_id TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)` — `kind ∈ {video, photo, pdf, document}`; `telegram_file_id` filled by the dispatcher on first Telegram send; `source_operator_file_id` set when promoted from a KB file.
  - `sales_followup_queue(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, project_id INTEGER NOT NULL, fire_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)` — `status ∈ {scheduled, sent, skipped_stale, cancelled_replied}`.
  - Indexes: `CREATE INDEX IF NOT EXISTS idx_services_project ON services(project_id, is_active)`, `idx_client_materials_project ON client_materials(project_id, is_active)`, `idx_followup_due ON sales_followup_queue(status, fire_at)`.
- `services/api/app/sales/state_repository.py` `StateRepository(*, db_path)`:
  - `get(chat_id) -> ConversationState | None`.
  - `upsert(*, chat_id, project_id, current_stage, collected_intent, last_proposal=None, last_customer_msg_at=None, last_bot_msg_at=None, now)` — atomic upsert keyed on `chat_id`.
  - `transition_stage(*, chat_id, new_stage, now)` — atomic stage update; raises `StateNotFound` if the row is missing.
  - `mark_customer_msg(*, chat_id, now)` / `mark_bot_msg(*, chat_id, now)` — timestamp-only updates.
  - `list_active(project_id) -> list[ConversationState]` — filtered to `current_stage != 'dormant'`.
- `services/api/app/sales/services_repository.py` `ServicesRepository(*, db_path)`:
  - `add(*, project_id, name, description_md=None, tags=None, now) -> int` (returns new id). `UNIQUE(project_id, name)` violation → raises `ServiceAlreadyExists`.
  - `list_active(project_id) -> list[Service]`.
  - `get(service_id) -> Service | None`.
  - `find_by_name(project_id, name) -> Service | None` (case-insensitive exact match — used by the concept-explainer in 12.06).
  - `soft_delete(service_id) -> None` (sets `is_active=0`).
  - `count_active(project_id) -> int` — used by the data-driven activation gate in 12.09.
- `services/api/app/sales/client_materials_repository.py` `ClientMaterialsRepository(*, db_path)`:
  - `add(*, project_id, kind, local_path, byte_size, duration_seconds=None, caption=None, tags=None, telegram_file_id=None, source_operator_file_id=None, now) -> int`.
  - `list_active(project_id) -> list[ClientMaterial]`.
  - `get(material_id) -> ClientMaterial | None`.
  - `pick_by_tags(*, project_id, tags) -> list[ClientMaterial]` — returns active rows whose `tags_json` overlap (any) the requested tag list, ordered by most-specific overlap first.
  - `update_telegram_file_id(*, material_id, telegram_file_id) -> None` — called by the dispatcher after a successful Telegram send.
  - `soft_delete(material_id) -> None`.
- `services/api/app/sales/followup_queue_repository.py` `FollowupQueueRepository(*, db_path)`:
  - `enqueue(*, chat_id, project_id, fire_at, now) -> int` — one scheduled row per chat (replaces an existing `scheduled` row for the same chat).
  - `due(*, now, limit=100) -> list[FollowupRow]` — rows with `status='scheduled' AND fire_at <= now`.
  - `mark_sent(row_id) -> None`, `mark_skipped_stale(row_id) -> None`, `mark_cancelled_replied(chat_id) -> int` (returns rows cancelled; called when the customer replies).
  - `list_for_chat(chat_id) -> list[FollowupRow]` — for `/sales_state`.
- Frozen dataclasses (mirror Epic-11 pattern): `ConversationState`, `Service`, `ClientMaterial`, `FollowupRow`, `Intent` (typed JSON shape: `dates`, `headcount`, `vehicle_count`, `difficulty`, `drivers` — each `str | int | None`).
- New `Settings` fields: `sales_db_path: str = ".data/semantaix_sales.db"`. `.env.example` entries added.
- Bootstrap registered in `services/api/app/main.py` startup (same hook as the other DB inits).

### Out of Scope
- The `SalesPersonaAnswerer` itself, command handlers, dispatch endpoint, scheduler job, analyzer, pipeline wiring — all later stories. Repos + schema + dataclasses only.
- Any admin UI for editing services/materials (later epic).
- The `pick_by_tags` LLM-driven ranking — v1 uses simple overlap count (most-specific first).

## Implementation Notes
- **Sync `sqlite3` repos** (project-context rule); callers dispatch via `asyncio.to_thread`. No raw SQL outside these repos.
- All public methods are **keyword-only** (`def f(self, *, x, y)`); `__init__` takes `*, db_path: str` per the project-context naming rule.
- Timestamps ISO-8601 UTC (`datetime.now(timezone.utc).isoformat()`); the `now` is **injected** into every write/query for deterministic tests (mirror `CalendarOAuthStateRepository.create`/`consume`).
- JSON columns store `json.dumps(..., ensure_ascii=False, sort_keys=True)` for stable diffs; the repo layer is the only place that handles JSON ↔ dataclass conversion.
- `tags_json` stores a list (e.g. `["квадрик","средний"]`); `pick_by_tags` parses + counts overlap in Python (the dataset is small; no full-text or JSON1 needed in v1).
- `client_materials.telegram_file_id` is `NULL` until the dispatcher (story 12.05) caches it; `update_telegram_file_id` is the only writer of that column.
- Bootstrap MUST be idempotent — calling `init_schema(db_path)` twice leaves rows intact.
- **Default-off invariant:** the schema creates the tables but no rows. The data-driven activation gate (story 12.09) calls `ServicesRepository.count_active(project_id) == 0 → silent no-op`.

## Test Plan
### Unit
- `tests/test_sales_state_repository.py` — `get` returns None on missing chat; `upsert` round-trip with all optional fields; `upsert` overwrites existing row atomically; `transition_stage` raises `StateNotFound` on missing chat; `mark_customer_msg` / `mark_bot_msg` update only their respective timestamp column; `list_active` excludes `dormant` rows; injected `now` is stored verbatim.
- `tests/test_sales_services_repository.py` — `add` round-trip with and without description/tags; `add` duplicate `(project_id, name)` raises `ServiceAlreadyExists`; `list_active` excludes soft-deleted; `find_by_name` is case-insensitive; `soft_delete` flips `is_active` and removes row from `list_active`; `count_active` is `0` on a fresh DB.
- `tests/test_sales_client_materials_repository.py` — `add` round-trip per `kind`; `pick_by_tags` overlap ranking (most-specific first); `pick_by_tags` returns empty list when no overlap; `update_telegram_file_id` updates only the `telegram_file_id` column; `soft_delete` removes from `list_active`; `source_operator_file_id` persists round-trip.
- `tests/test_sales_followup_queue_repository.py` — `enqueue` inserts; second `enqueue` for the same `chat_id` replaces the prior `scheduled` row (no duplicates); `due` returns only `scheduled AND fire_at <= now`; `mark_sent` / `mark_skipped_stale` / `mark_cancelled_replied` transition status; `mark_cancelled_replied` cancels only `scheduled` rows (not already-sent).

### Integration
- `tests/test_sales_schema_bootstrap.py` — calling `init_schema(db_path)` twice leaves tables and rows intact (idempotent); all 4 tables + 3 indexes are present after first call; `PRAGMA foreign_keys` discipline matches the other one-file-per-concern DBs.

## Automated E2E verification
- None for this story (no externally observable behavior). Coverage is unit/integration-level.

## Manual Verification
1. `docker compose up --build api` → confirm `.data/semantaix_sales.db` exists with the four tables (`sqlite3 .data/semantaix_sales.db ".tables"` shows `client_materials`, `sales_conversation_state`, `sales_followup_queue`, `services`).
2. Confirm every project reads as sales-dormant by default: `sqlite3 .data/semantaix_sales.db "SELECT COUNT(*) FROM services;"` returns `0`.

## Done Criteria
- 100% coverage on the four new repo modules + dataclasses + bootstrap.
- `ruff check .` passes.
- Bootstrap is idempotent and default-off (zero `services` rows).
- All public methods keyword-only; no raw SQL outside the repos; sync `sqlite3` only.
- `Settings.sales_db_path` added with default; `.env.example` entry present.
