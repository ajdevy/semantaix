# Story 10.01 — Projects and Operators schemas + bootstrap + additive ALTERs

## Objective
Land the foundational entities that every later Epic 10 story builds on: `projects` and `operators` tables with their repositories, the admin auth + admin NL ops tables (schema-only — the workflow lands in 10.02 and 10.05), and additive `project_id` columns on `rag_chunks`, `knowledge_moderation_candidates`, and `operator_files`. Auto-bootstrap a `default` project and an operator row for `settings.hitl_primary_operator_username` so existing operator flows keep working unchanged.

## Scope

### In Scope
- New module `services/api/app/projects.py` with `ProjectRepository`:
  - `init_schema(db_path)`
  - `Project` dataclass: `id, slug, name, description, created_at, updated_at`
  - `create(slug, name, description=None) -> Project`
  - `get_by_slug(slug) -> Project | None`
  - `get(project_id) -> Project | None`
  - `list_all() -> list[Project]`
  - `update(slug, *, name=None, description=None) -> Project`
  - `delete(slug)` — raises if any operator or knowledge candidate references it
  - `ensure_default_project() -> Project` (idempotent, returns row for slug `"default"`)
- New module `services/api/app/operators.py` with `OperatorRepository`:
  - `init_schema(db_path)`
  - `Operator` dataclass: `id, username, chat_id, project_id, display_name, is_active, created_at, updated_at`
  - `create(username, project_id, *, chat_id=None, display_name=None) -> Operator`
  - `find_by_username(username) -> Operator | None`
  - `list_active() -> list[Operator]`
  - `update(username, *, project_id=None, chat_id=None, display_name=None, is_active=None) -> Operator`
  - `ensure_default_operator(username, project_id, chat_id=None) -> Operator` (idempotent)
- New module `services/api/app/admin_auth.py` — **schema only** in this story (`init_schema` for `admin_login_codes` + `admin_sessions`). Real `request_code`/`consume_code`/`create_session`/`validate_session` lands in 10.02.
- New module `services/api/app/admin_nl_ops.py` — **schema only** in this story (`init_schema` for `admin_nl_op_sessions`). The propose/confirm/cancel workflow lands in 10.05.
- Settings additions in `platform_common/settings.py`:
  - `projects_db_path: str = ".data/semantaix_projects.db"`
  - `operators_db_path: str = ".data/semantaix_operators.db"`
  - `admin_session_db_path: str = ".data/semantaix_admin_sessions.db"`
  - `admin_telegram_username: str = "@ajdevy"`
  - `admin_login_code_ttl_seconds: int = 300`
  - `admin_session_ttl_seconds: int = 86400`
- Additive idempotent `project_id INTEGER` columns via the `PRAGMA table_info` guard pattern:
  - `services/api/app/rag.py` — extend `init_schema` and add `idx_rag_chunks_project` index. `RagChunk` gains an optional `project_id` field (reader, ingest/retrieve untouched in this story — modified in 10.06).
  - `services/api/app/knowledge_moderation.py` — append `("project_id", "INTEGER")` to `_OPERATOR_UPLOAD_COLUMNS`; extend `KnowledgeCandidateRow` and `_SELECT_COLUMNS`.
  - `services/bot_gateway/app/operator_files.py` — additive `project_id INTEGER` on `operator_files`; extend `OperatorFileRecord` field list.
- Wire bootstrap into api startup (`services/api/app/main.py` startup event):
  1. `project_repository.init_schema()` + `ensure_default_project()`.
  2. `operator_repository.init_schema()` + `ensure_default_operator(settings.hitl_primary_operator_username, default.id, settings.hitl_primary_operator_chat_id)`.
  3. `admin_auth_repository.init_schema()`.
  4. `admin_nl_ops_repository.init_schema()`.

### Out of Scope
- Any new HTTP endpoint (10.02 onward).
- Any web UI page (10.03).
- Any Telegram command (10.04).
- Any NL dialog logic (10.05).
- Any change to `RagRepository.ingest` / `retrieve` signatures (10.06).
- Any change to `bot_gateway` operator resolution beyond reading `project_id` if it exists in `operator_files` (10.07).

## Implementation Notes
- Reuse the existing `_connect(db_path)` pattern from `services/api/app/rag.py:11` (or `knowledge_moderation.py:9`) — `sqlite3.connect(..., detect_types=...)`, `PRAGMA foreign_keys`, `row_factory = sqlite3.Row`.
- All ALTER columns must be guarded by `PRAGMA table_info` inspection so re-running init is safe (pattern: `services/api/app/knowledge_moderation.py:51-72`).
- `delete` on `ProjectRepository` queries the `operators` + `knowledge_moderation_candidates` + `operator_files` tables — but those tables live across different DB files, so the safe move is for `delete` to take an optional `referenced_check: Callable[[int], bool]` (or accept that the only safe deletion is via a higher-level service in api/main.py). For this story, the simplest correct version: `delete(slug)` checks `operators` table only (same api process), and refuses if `operators.project_id == project.id` exists. Knowledge/file orphan handling is out-of-scope for 10.01.
- `Operator.username` always stored verbatim (including the `@` prefix when given) — matches `hitl_primary_operator_username` convention.
- `is_active` stored as `INTEGER NOT NULL DEFAULT 1`; convert to bool on read.

## Test Plan

### Unit
- `tests/test_projects_repository.py` — schema init idempotent, create + get_by_slug round-trip, duplicate slug raises, update changes fields and bumps `updated_at`, `list_all` order is stable, `delete` raises when operators reference, `ensure_default_project` is idempotent.
- `tests/test_operators_repository.py` — schema init idempotent, create requires project_id, find_by_username returns None for unknown, update flips is_active, `list_active` excludes inactive, `ensure_default_operator` idempotent and updates chat_id when changed.
- `tests/test_admin_auth_repository.py` — `init_schema` creates both tables and is idempotent (workflow tests deferred to 10.02).
- `tests/test_admin_nl_ops_repository.py` — `init_schema` creates the table and is idempotent (workflow tests deferred to 10.05).
- `tests/test_rag_repository_schema.py` — `init_schema` accepts and persists `project_id` column when present (insert row via raw SQL with project_id, SELECT shows it). `RagRepository.ingest` / `retrieve` unchanged in this story.
- `tests/test_knowledge_moderation_schema.py` — `init_schema` adds `project_id` column; `create_approved_operator_upload` accepts no new args this story, but `_OPERATOR_UPLOAD_COLUMNS` exposes `project_id`.
- `tests/test_operator_files_schema.py` — additive `project_id` column on `operator_files`; existing `record_upload` continues to work without it.

### Integration
- `tests/test_api_startup_bootstrap.py` — starting the api app over fresh temp paths creates `default` project (id=1) and an operator row for `settings.hitl_primary_operator_username`.

## Automated E2E verification
Deferred to 10.02.

## Manual Verification
1. Delete `.data/semantaix_projects.db` and `.data/semantaix_operators.db`.
2. `docker compose up --build api`.
3. Inspect: `sqlite3 .data/semantaix_projects.db "SELECT * FROM projects"` shows one row with slug `default`.
4. Inspect: `sqlite3 .data/semantaix_operators.db "SELECT username, project_id FROM operators"` shows `@ajdevy` (or whatever `hitl_primary_operator_username` is) bound to `project_id=1`.
5. Inspect: `sqlite3 .data/semantaix_rag.db "PRAGMA table_info(rag_chunks)"` lists `project_id INTEGER`.

## Done Criteria
- All unit + integration tests pass.
- 100% coverage on new modules (`projects.py`, `operators.py`, `admin_auth.py`, `admin_nl_ops.py`) and on the touched ALTER paths.
- `ruff check .` passes.
- Bootstrap is idempotent — re-running api startup leaves the DB untouched (no duplicate rows, no schema drift).
- Existing tests in `services/` and `tests/` continue to pass (no regressions).
