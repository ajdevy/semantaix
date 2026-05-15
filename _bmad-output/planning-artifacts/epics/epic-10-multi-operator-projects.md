# Epic 10: Multi-Operator + Projects + Admin Surface

## Goal
Promote `Project` to a first-class entity that groups operators and knowledge files. Add multi-operator support, an admin surface (web UI + Telegram commands + natural-language bot dialog) gated by a one-time login code DM'd via the bot, and scope RAG retrieval by `project_id` so each customer answer is grounded only in the project that owns the conversation.

## In Scope
- `projects` and `operators` SQLite-backed entities with idempotent schemas, default-row bootstrap on api startup, and additive `project_id` columns on `rag_chunks`, `knowledge_moderation_candidates`, and `operator_files`.
- Admin authentication via one-time 6-digit code DM'd through the existing `TelegramBotSender` to the admin username configured in `Settings`, plus opaque 24-hour session tokens stored sha256-hashed in a dedicated DB.
- Admin web UI pages under `/admin/*`: dashboard, projects CRUD, operators CRUD, files list with per-row project reassignment. Inline HTML in the existing `services/web_ui/app/main.py` style; cookie-gated middleware.
- Admin Telegram slash commands (`/projects`, `/project_new`, `/operator_add`, `/operator_remove`, `/operator_list`, `/file_assign`) gated by `settings.admin_telegram_username`.
- Admin natural-language dialog in the bot (`"создай проект …"`, `"привяжи файл #X к проекту …"`, etc.) using a propose/confirm/cancel state machine mirroring `nl_knowledge_ops` but in a separate `admin_nl_op_sessions` table.
- RAG retrieval scoping: `RagRepository.retrieve(project_id=...)` filters chunks to `project_id = ? OR project_id IS NULL`; `/conversations/inbound` resolves the project via the operator binding on the open HITL ticket, falling back to the default project.
- Multi-operator resolution in `bot_gateway`: every operator-only command consults the `operators` registry over the api; `settings.hitl_primary_operator_username` remains as fallback for bootstrap and is auto-registered to the default project.

## Out of Scope
- RBAC beyond admin/operator (no per-project operator scoping yet, no read-only roles).
- Hard project deletion with cascade — `DELETE /projects/{slug}` is allowed only when the project owns no operators and no files.
- Cross-project search or per-customer project routing (customer flow without an open ticket defaults to project_id=1).
- Editing or rewriting historical RAG chunks beyond the additive `project_id` column.
- Admin login via passwords, SSO, or any auth that is not the Telegram-DM code.
- Per-operator analytics dashboards (out-of-scope for the admin surface).

## Dependencies
- **Epic 04** — HITL operator identity (`hitl_primary_operator_username`, `/hitl_config`) and `hitl_runtime_config` runtime override pattern.
- **Epic 05** — `RagRepository` ingest/retrieve and `rag_chunks` schema.
- **Epic 06** — Knowledge moderation schema and `create_approved_operator_upload`.
- **Epic 08** — NL operations propose/confirm/cancel state machine in `nl_knowledge_ops` and the admin gate (`_check_nl_ops_admin`).
- **Epic 09** — Operator upload pipeline through `OperatorUploadRequest` + `OperatorFileRepository`.

## Exit Criteria
- Default project and default operator (the primary one) auto-exist after a clean `docker compose up`; pre-existing chunks remain retrievable (NULL-project fallback).
- Admin opens `/admin/login`, requests a code, receives a 6-digit DM from the bot, enters it, and lands on a cookie-gated dashboard listing project count and operator count.
- Admin creates a project, adds a second operator with `chat_id` to that project, reassigns an existing file to the project — all three surfaces (web UI form, slash command, natural-language dialog) succeed and yield the same DB state.
- A customer message routed through a HITL ticket assigned to the second operator returns RAG answers grounded only in the second operator's project chunks; the primary operator's chunks are not surfaced.
- All existing operator flows from Epics 04/06/09 keep working: `/hitl_config`, `/kb_add`, `/files`, `/send`, and inbound HITL escalation continue to behave identically when only the primary operator is registered.
- `ruff check .` clean; `pytest --cov` shows 100% line coverage on the new modules in `platform_common/` and `services/`.

## Automated E2E verification
- Story-aligned tests under `tests/e2e/test_e2e_epic10_*.py` (`@pytest.mark.e2e`).
- New scripted signoff: `scripts/epic10_signoff.sh`.
- Matrix updated in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
