# Semantaix PRD

## 1. Product Overview

Semantaix is a Telegram-based AI assistant for customer support/sales that uses RAG to answer questions, escalates uncertain requests to a human operator, and continuously improves knowledge through moderation workflows.

This PRD is scoped to the confirmed Option B implementation strategy. The MVP
shipped on a SQLite-backed persistence model with lemma-overlap retrieval; the
items below reflect the **as-built** stack:

- FastAPI-centered microservices behind an nginx reverse proxy
- Docker-first deployment model
- **SQLite** as the system of record (one DB file per concern under `.data/`)
- **Lemma-overlap retrieval** (Russian normalizer); Qdrant is provisioned in
  compose and health-checked but not on the retrieval path, and Postgres is
  available behind a compose profile but unused at runtime
- Human-in-the-loop (HITL) fallback
- Strong monitoring/logging/health checks
- DigitalOcean-ready operations baseline

## 2. Goals and Non-Goals

### 2.1 Goals

- Provide grounded AI responses for Telegram users via retrieval.
- Ensure reliable fallback to human operators when confidence is low.
- Persist full dialog history for auditability and operations.
- Extract only useful dialog knowledge for RAG indexing (noise-filtered).
- Provide robust incident visibility in Web UI and Telegram alerts to `@ajdevy`.
- Run all feasible components in Docker with reproducible local/prod behavior.

### 2.2 Non-Goals

- Advanced BI analytics dashboards.
- Complex enterprise RBAC beyond practical admin/operator needs (per-operator credential ownership — e.g. an operator connecting their own calendar — is in scope and is not RBAC).
- Additional **customer channels** beyond Telegram. (External *integrations/data sources* such as weather and Google Calendar are in scope; the customer-facing channel remains Telegram-only.)
- **Calendar write/booking (event creation).** The calendar capability is **read-only availability first** (see FR-18–FR-22); creating or modifying calendar events is deferred to a later phase.

> **Note (post-MVP reconciliation):** "Multi-tenant architecture" was an original MVP non-goal but project- and multi-operator scoping shipped post-MVP (Epics 08 and 10). Project-scoped capabilities — including the opt-in calendar feature — build on that delivered scoping.

## 3. Personas

- Telegram End User: asks support/sales questions.
- Operator: answers escalated conversations.
- Moderator/Admin: reviews extracted knowledge, manages settings, handles incidents.
- On-Call Owner (`@ajdevy`): receives critical operational alerts.

## 4. Functional Requirements (FR)

### FR-1 Telegram Conversation Flow

- Bot receives user messages via webhook.
- System loads conversation context and attempts AI answer.
- AI answer is returned when sufficient confidence and retrieval grounding exist.

Acceptance criteria:

- Telegram message is processed within configured latency target in healthy state.
- Response payload is persisted with role + trace metadata.

### FR-2 RAG Retrieval and Answering

- System retrieves relevant context via lemma-overlap scoring over indexed chunks
  (`rag_chunks`) and composes the response prompt. (Qdrant remains provisioned for a
  future embedding-based retrieval path but is not used today.)
- Responses must be grounded in retrieved content when available.

Acceptance criteria:

- Retrieval pipeline logs top context candidates with trace ID.
- Guardrail policy enforces fallback when grounding/confidence is below threshold.

### FR-3 Human-in-the-Loop Escalation

- If AI cannot answer confidently, a durable escalation ticket is created.
- Escalation routed to configurable primary Telegram username.
- Operator response is mapped back to originating user and delivered as a bot-authored message.

Acceptance criteria:

- Escalation ticket lifecycle states are persisted (`open` → `assigned` → `resolved`; operator reply auto-resolves).
- Mapping from operator reply to user conversation is deterministic and auditable.
- End-user delivery does not expose operator username or Telegram forward metadata.

### FR-4 Configurable HITL Recipient

- Web UI Settings allows updating primary Telegram recipient for fallback routing.
- Telegram command path also allows runtime updates via bot command:
  - `/hitl_config @username <chat_id>`
- Runtime bot configuration is admin-gated by configured username (`HITL_CONFIG_ADMIN_USERNAME`, currently `@ajdevy`).

Acceptance criteria:

- Setting update persists in DB and is used without service restart.
- Invalid Telegram username format is rejected with clear error.
- Non-admin command attempts are ignored and audit/logged as unauthorized configuration attempts.

### FR-5 Full Transcript Storage + Knowledge Candidate Extraction

- All conversation messages are stored in SQLite (`semantaix_story1.db`:
  `conversations`, `messages`).
- Separate extraction pipeline generates `knowledge_moderation_candidates` from
  useful snippets only.
- Noise (small talk/duplicates) is filtered before candidate creation.

Acceptance criteria:

- Full transcript remains intact regardless of extraction.
- Only approved candidates are eligible for vector indexing.

### FR-6 Knowledge Moderation Workflow

- Moderators can review candidates/drafts, edit, approve, reject.
- Approval triggers re-index workflow into vector store.

Acceptance criteria:

- Every moderation action is audit logged.
- Approved knowledge becomes retrievable in subsequent RAG queries.
- Rejected candidates are excluded from indexing but retained for audit/history.

### FR-7 Alerts and Incident Management UI

- Web UI contains Alerts tab with:
  - read/unread status
  - filters by severity/source/status
  - acknowledge/resolve actions
  - incident event timeline

Acceptance criteria:

- Incident state transitions persist and survive page refresh.
- UI accurately reflects deduplicated incident records.

### FR-8 Critical Telegram Incident Notifications

- Critical incidents trigger Telegram notifications to `@ajdevy`.
- The incident engine is generic (fingerprint-based dedup); illustrative critical
  sources include:
  - provider 429 spikes
  - provider 5xx spikes
  - data-store / dependency unavailability (e.g., Qdrant readiness failure)
  - HITL delivery failures
  - failed answer-trace persistence (per FR-15)

Acceptance criteria:

- Alerts are deduplicated/throttled by policy window.
- Delivery status is recorded in the `incident_events` history (`telegram_notify`).

### FR-9 Health Endpoints

- Services expose `/health/live`, `/health/ready`, `/health/startup`.
- Readiness reflects dependency checks.

Acceptance criteria:

- When dependency fails, `ready` degrades while `live` can remain healthy.
- Health behavior is covered by automated tests.

### FR-10 Structured Logging and Trace Correlation

- Logs are structured JSON and include:
  - `trace_id`
  - `conversation_id`
  - `escalation_ticket_id` (when applicable)
  - `incident_id` (when applicable)

Acceptance criteria:

- A full user journey can be traced across bot/API/worker logs.

### FR-11 Resilience for External Providers

- Implement retry with exponential backoff + jitter, rate-limit handling, circuit breaker.

Acceptance criteria:

- Repeated provider failures trigger expected breaker behavior.
- System enters degraded mode and falls back to HITL policy when needed.

### FR-12 Docker-First Runtime and Deployment

- All feasible services are containerized.
- Compose stack supports local/dev/prod parity.

Acceptance criteria:

- Services build and run via `docker compose`.
- Health checks are declared per service in compose/runtime.

### FR-13 Answer Guardrail Decision Engine

- System evaluates generated answers against explicit validity checks before delivery.
- If checks fail, system escalates to HITL instead of sending uncertain AI output.

Acceptance criteria:

- Validity decision and failed check reasons are logged with trace metadata.
- Decision contract includes retrieval sufficiency, grounding, confidence, and safety checks.

### FR-14 Backup and Restore Operations

- System performs backups of the SQLite system-of-record as **tar.gz archives** of
  the DB files. (The original plan scoped this to Qdrant snapshots; as built it backs
  up the SQLite stores that hold the live data.)
- Web UI shows backup list, last successful backup timestamp, and storage location.
- Web UI provides restore action with token confirmation and status reporting.

Acceptance criteria:

- Backup runs persist metadata in `semantaix_backups.db` (`backups` + `backup_events`).
- Last backup timestamp and archive path are visible in UI.
- Restore operation requires a confirmation token and is auditable, reporting
  `restore_completed` / `restore_failed` events.

### FR-15 Tenant-Scoped Answer Transparency (“Why This Answer”)

- For delivered or policy-blocked AI paths tied to a stored end-user message, the system persists a queryable **answer trace** capturing retrieval lineage (chunk references and scores), guardrail/policy outcome, model routing metadata, and an MVP grounding/confidence snapshot.
- Tenant-authorized Web UI users can open a conversation message and view that trace (read-only).

Acceptance criteria:

- Trace records are durable and **append-only** (corrections create new knowledge versions; they do not rewrite historical traces). As built, `answer_traces` is a single global store (not tenant-partitioned); the trace-originated correction loop (`trace_corrections`) is tenant-scoped.
- Missing or failed trace persistence raises an operational incident per the Epic 02 backbone.

*Delivery:* see **Epic 08** (`epic-08-tenant-knowledge-ops-and-answer-traces.md`), Story 08.01–08.02; builds on **Epic 05** retrieval payloads and **Epic 03** guardrail decision fields.

### FR-16 Natural-Language Tenant Knowledge Operations

- Paying clients (tenants) can create, update, or retire tenant knowledge through a **conversational** flow (bot-first), including preview, explicit confirmation, versioning, reindex enqueue, and full audit logging.
- Tenants may be configured so mutating NL operations create **moderation candidates** instead of immediate publish, reusing **FR-6 / Epic 06** when strict quality gates apply.

Acceptance criteria:

- No silent writes: destructive or ambiguous intents require clarification or explicit confirm.
- Every successful or abandoned mutating session leaves **audit_logs** evidence.

*Delivery:* **Epic 08**, Story 08.03; indexes through **Epic 05**; optional candidate path through **Epic 06**.

### FR-17 Trace-Originated Knowledge Correction Loop

- From a specific answer trace, tenant users can initiate a guided correction that updates future retrieval behavior, with optional moderation handoff, reindex completion signaling, and cross-linked audit history.

Acceptance criteria:

- Correction flow links trace → draft/candidate → approval (when moderation on) → reindex outcome, without altering past traces.
- Failures enqueue incidents and surface user-visible retry or support state where appropriate.

*Delivery:* **Epic 08**, Story 08.04; moderation mechanics per **Epic 06**.

### Feature Group: Calendar Availability & Scheduling (Epic 11)

Read-only availability first: the bot answers customer questions like "is service X available at date/time Y" by combining a calendar operator's Google Calendar free/busy with per-service scheduling rules. The feature is **opt-in per project and default-off** — most projects never enable it, and when disabled it is a silent no-op in the answer pipeline. Booking/event creation is explicitly out of scope for this phase (see §2.2).

**v1 scoping decisions (validation 2026-05-22).** To keep the data model and answerer tractable: (1) a calendar-enabled project designates exactly **one "calendar operator"** whose connected calendar answers availability questions — multi-operator selection is **deferred**; (2) all customer and rule times are interpreted in the **project timezone** (`calendar_project_settings`); (3) only the calendar operator's **primary** Google calendar defines busy — multi-calendar selection is **deferred**; (4) v1 makes **one live `freeBusy` call per question** (no result caching) to avoid stale "free" answers.

### FR-18 Operator Google Calendar Connect (OAuth)

- The project's designated **calendar operator** connects their own Google Calendar from Telegram via a slash command (e.g. `/connect_calendar`), gated to authorized operators.
- The bot DMs a Google OAuth consent URL scoped **read-only** (`calendar.readonly` / free-busy). Google redirects to a callback endpoint that validates a **single-use, server-stored `state` token** (TTL mirrors the existing login-code: ~5 min, consumed on first use). Because the browser hitting the callback is **not** Telegram-authenticated, `state` is the sole binding between the browser callback and the initiating operator. The callback exchanges the auth code, stores an encrypted refresh token (**upsert on `(project, operator)`** — re-consent overwrites), and is **rate-limited** (unauthenticated endpoint that triggers token exchange); it renders a simple success/failure page to the operator's browser.
- **Connect IS enable.** A successful callback also flips the project to `enabled=1` and records the connecting operator as the designated calendar operator — atomically with the token upsert. There is no separate enable command or endpoint; an operator implicitly enables their project by connecting. If the enable write fails after the token upsert, the callback surfaces a 500-class error rather than rendering a misleading success page (the operator can retry by re-running `/connect_calendar`).
- Access tokens are minted on demand and cached until near-expiry.
- **Revocation & long-term expiry handling:** a refresh that fails — operator revoked access on Google, or the refresh token expired (Google's 7-day "Testing"-status, 6-month-unused, or per-client token-cap rules) — is detected on next use. The operator transitions to a **"reconnect needed"** state, is proactively notified via Telegram to re-run `/connect_calendar`, an **incident is emitted** (Epic-02 integration), and the dead token row is **cleared** (never left as a poison row). No customer-visible error.
- Disconnect (**operator-only**): best-effort call to Google's token-revocation endpoint, then delete the local token regardless (if revoke fails, still delete locally and log). Connecting and disconnecting are auditable operator actions; an admin cannot disconnect (admins may only disable per FR-21).

Acceptance criteria:

- Successful consent → stored encrypted refresh token + project enabled with the connecting operator recorded as the designated calendar operator (atomic with token storage) + Telegram confirmation. A forged, expired, replayed, or unmatched `state` is rejected and nothing is stored or enabled.
- For an already-enabled project, a re-connect preserves the existing `project_timezone` / `lookahead_days` and only updates the designated operator.
- `state` is single-use (consumed on first callback) and expires after its TTL.
- A revoked/expired refresh token is detected on next use → reconnect state + operator notification + incident emitted + token cleared, with no customer-visible error.
- Re-connecting the same operator overwrites the prior token (one row per `(project, operator)`).
- Tokens, secrets, and the encryption key never appear in logs or answer-trace metadata.

*Delivery:* **Epic 11.**

### FR-19 Calendar Availability Answering

- For a calendar-enabled project, when a customer's question resolves to a configured service (**FR-22**), the system makes one live `freeBusy` call against the calendar operator's **primary** Google calendar over the look-ahead window, **intersects it with the per-service rules of FR-20** (sourced from `project_services` rows where `duration_minutes IS NOT NULL`), interprets all times in the **project timezone**, and answers in Russian.
- The customer states a start time; availability requires a free block **`[start, start + duration)`** that also falls within the service's working hours / service-days. The look-ahead horizon is a per-project config value (default 60 days).
- Availability reflects only free/busy blocks; the bot never echoes event titles or other calendar content into customer-facing answers.
- When availability cannot be computed confidently (provider error, token revoked / reconnect-needed), the request **escalates to HITL routed to the project's calendar operator** with context ("availability question; calendar error/uncertainty"). A wrong "yes, it's free" is treated as worse than an escalation.

Acceptance criteria:

- A slot that is busy on the calendar, OR has no free `[start, start+duration)` block, OR falls outside the service's working hours / service-days / date exceptions → reported **not available**.
- A slot that is free for the full duration **and** satisfies all service rules → reported **available**.
- Any provider/token failure → **escalation to the calendar operator** (single deterministic branch; the customer receives the standard HITL acknowledgement), never a fabricated availability answer.
- All quoted customer-facing strings in this feature group are **illustrative**; the actual copy is Russian and configured as data (per the Russian-first-content-is-DATA rule).

*Delivery:* **Epic 11.**

### FR-20 Per-Service Scheduling Rules

- Scheduling fields are the **calendar-eligible subset** of the canonical project services catalog (**FR-23**): each row in `project_services` that carries `duration_minutes IS NOT NULL` is schedulable. The schedulable fields are: service name (resolved per FR-22), duration, working-hours windows (**one or more per day**, e.g. to model a lunch break), recurring **service-days** (days of week), and **date-level exceptions/closures** (honoring RU public holidays via the existing `holidays` library).
- Rules are runtime configuration (the `hitl_runtime_config` config-in-DB pattern), editable without code changes, scoped per project; in v1 all services map to the single project calendar operator.

Acceptance criteria:

- Availability answers (FR-19) honor the configured duration, working-hours windows, service-days, and date exceptions/holiday closures, evaluated in the project timezone.
- Changing a service rule changes subsequent availability answers without redeploy.

*Delivery:* **Epic 11.**

### FR-21 Per-Project Opt-In Gating

- The calendar capability is **default-off**; a project must explicitly enable it **and** designate a calendar operator.
- The answer pipeline treats calendar as a tri-state: **(a) not enabled** → silent no-op (the calendar logic declines, the pipeline proceeds normally, no error); **(b) enabled but the calendar operator is not connected / in reconnect state** → a "calendar isn't connected yet" reply and/or HITL escalation, never a 500; **(c) connected** → compute and answer per FR-19.
- **Enable / disable / disconnect (permission model):** there is no separate enable command or endpoint — **`/connect_calendar` IS the enable action.** A successful OAuth callback flips the project to enabled and records the connecting operator as the designated calendar operator (FR-18). This means an operator implicitly enables their project by connecting, and an admin cannot enable a project without an operator's consent — by design. **"Disable" turns the feature off but keeps the stored token; both the operator and an admin may disable** (`/calendar_off`). **Re-enable after disable = the operator re-runs `/connect_calendar`** (which re-runs Google consent, refreshes the token, and re-flips `enabled=1`). **"Disconnect/delete" removes the integration and deletes the stored token (FR-18) — operator-only.** An admin can pause the integration but cannot enable it and cannot delete the operator's connected calendar.
- The enablement check is a **single cached project-settings read performed before intent detection or any API call**; its overhead is negligible. (The exact pipeline placement — a standalone answerer vs a `scheduling_context` signal — is decided in the architecture step, but the "config check precedes intent/API work" ordering is the binding requirement.)

Acceptance criteria:

- On a project with calendar disabled, calendar logic adds no customer-visible behavior, and the project-settings check precedes intent detection and any API call.
- An operator running `/connect_calendar` and completing Google consent makes the project enabled and availability answers live (atomic with token storage); disabling reverts to the no-op state without deleting the stored token; re-running `/connect_calendar` re-enables.
- Both the operator and an admin can disable; an admin attempting to disconnect/delete the integration is rejected (operator-only); there is no admin enable path.

*Delivery:* **Epic 11.**

### FR-22 Service Resolution from Russian Text

- Map a customer's free Russian text to a configured **schedulable** service via **lemma matching** (the existing `RussianNormalizer`), not raw string equality. **"Schedulable"** here means a `project_services` row (per FR-23) with `duration_minutes IS NOT NULL` — i.e. the calendar-eligible subset only; catalog-only rows (no duration) are intentionally invisible to the calendar resolver to avoid "yes, маникюр exists" → "but I can't book it for you" answers.
- The lemma matcher runs against the **project-scoped** `project_services` calendar-eligible subset for `ctx.project_id`; results from other projects' rows never surface.
- **No match** (named service isn't configured) or **date/time given but no service named** → ask **one** clarifying question; if still unresolved → escalate to HITL.
- **Ambiguous match** (multiple services match — including duplicate-lemma collisions like "стрижка мужская" / "стрижка детская") → ask **one** disambiguating question; if still ambiguous → escalate to HITL.
- The system never guesses a service.

Acceptance criteria:

- A lemma match to exactly one configured **schedulable** service (`duration_minutes IS NOT NULL`) resolves to that service.
- A lemma match that hits only catalog-only rows (`duration_minutes IS NULL`) is treated as **no match** for scheduling purposes.
- No-match / ambiguous-match / no-service-named triggers exactly **one** clarifying turn before escalation; unresolved after that clarification escalates (never silently picks a service).

*Delivery:* **Epic 11.**

### Feature Group: Unified Project Services Catalog (Epic 12)

One canonical operator-curated `project_services` table per project drives BOTH the catalog answer ("какие услуги?") AND the calendar availability flow. Rows are catalog-only when scheduling fields are absent, calendar-only when only the calendar uses them, and dual-use in the common case where a single offering is both advertised and bookable. The catalog answer reads structured services first and **merges** with the existing LLM digest path (deduplicating services that appear in both — the structured row wins on conflict because it is more authoritative); the digest is consulted in full only when the structured table is empty for that project. Services are editable by operators (and admins on projects where they are registered operators) through two converging paths: a slash command and a Russian natural-language dialog with explicit preview/confirm. This eliminates the prior duplication where the same offering ("маникюр") had to be described once as a calendar service rule and again indirectly via an uploaded PDF.

**Out of scope for Epic 12** (deferred — see decision log):
- LLM-based extraction of services from `/kb_add`-uploaded PDFs into `project_services` rows (future epic).
- Web admin UI for `project_services` CRUD (Epic 12 is bot-only — slash + NL).
- Multi-operator / multi-calendar selection (Epic 11 deferral, unchanged here).
- Booking / event creation (still the §2.2 Non-Goal).

### FR-23 Canonical `project_services` table

- Rename `calendar_service_rules` → `project_services` in the same SQLite DB (`.data/semantaix_calendar.db`).
- **Migration is genuinely idempotent via existence-check guards**, not blind ALTER. Spec: (a) check `SELECT name FROM sqlite_master WHERE type='table'` — if `calendar_service_rules` exists and `project_services` does not, run `ALTER TABLE calendar_service_rules RENAME TO project_services`; if `project_services` already exists, skip the rename; (b) for each new column, query `PRAGMA table_info(project_services)` and `ADD COLUMN` only when the column is absent; (c) **fresh-deploy path**: if neither `calendar_service_rules` nor `project_services` exists, `CREATE TABLE project_services` directly with the final schema (no requirement that Epic 11's rules-table migration has run first). Migration touches **only** `calendar_service_rules` → `project_services` rename and the four new columns; the other tables in `semantaix_calendar.db` (`calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state`) are unchanged by this migration.
- Final columns: `id, project_id, name (REQUIRED), description, price_text (free-form, e.g. "от 2 000 ₽"), tags_json, duration_minutes, working_hours_json, service_days_json, date_exceptions_json, updated_at`.
- **Uniqueness:** `UNIQUE(project_id, lower(name))` — one row per `(project_id, case-insensitive name)`.
- **JSON column shapes (pinned for renderer + slash + NL extractors):**
  - `working_hours_json`: `{"mon":[["10:00","19:00"]], "tue":[["10:00","13:00"],["14:00","18:00"]]}` — per-weekday list of `[start, end]` windows (multiple windows per day model lunch breaks per FR-20).
  - `service_days_json`: `["mon","tue","wed","thu","fri","sat"]` — lowercase 3-letter weekday codes (matches Epic 11's existing convention).
  - `date_exceptions_json`: `["2026-01-01","2026-05-09"]` — list of ISO date strings (closures / holiday exceptions on top of RU public holidays from the `holidays` library).
  - Russian rendering map for these shapes lives in a new data file `data/russian_calendar_terms.json` (per the Russian-first-content-is-DATA rule — day codes → "пн"/"вт"/..., month-day formatting, exception phrasing).
- A row is **calendar-eligible iff `duration_minutes IS NOT NULL`**. The calendar code filters on that predicate; rows without a duration are catalog-only.
- A new `ProjectServiceRepository` (sync `sqlite3`, dispatched via `asyncio.to_thread`) is the canonical CRUD seam for both the catalog answer and the calendar resolver. **`ProjectServiceRepository.upsert` is keyed on `(project_id, lower(name))`** — duplicate-name attempts via slash or NL **update** the existing row (upsert semantics) and emit a `services_upsert_duplicate_name` structured-log event.
- **Concurrency:** per-`(project_id, lower(name))` `asyncio.Lock` around `ProjectServiceRepository.upsert` (single-flight, mirroring Epic 11's per-operator calendar-token refresh lock). Combined with the uniqueness constraint, same-row add-vs-add races serialize and the second writer wins (last-writer-wins is acceptable for operator-curated content). Add-vs-delete races resolve to the last operation's intent. **No optimistic concurrency / `updated_at` precondition checks in v1.**
- `CalendarSettingsRepository`'s service-rule method names remain as delegating aliases (`upsert_service_rule`, etc.) until the **Epic 13 cleanup PR (no later than 60 days after Epic 12 merge)**; deprecated paths log a `deprecation_warning_calendar_settings_service_rule` event.

Acceptance criteria:

- **Idempotency:** running the migration twice on the same DB is a no-op the second time (no `duplicate column name`, no `no such table`).
- **Fresh deploy:** running the migration on a DB where neither `calendar_service_rules` nor `project_services` exists succeeds and produces `project_services` with the final schema, without requiring any Epic 11 migration to have run first.
- After migration, `calendar_service_rules` no longer exists; `project_services` has all listed columns; the `UNIQUE(project_id, lower(name))` constraint is enforced (a duplicate insert raises `IntegrityError` if attempted directly; `upsert` converts that into an UPDATE); the `project_services_project_idx` index exists; existing calendar tests pass against the new table; secret/PII handling unchanged.
- A row inserted with only `name` is valid and visible to the catalog answer; it is not visible to the calendar resolver until `duration_minutes` is set.
- The other tables in `semantaix_calendar.db` (`calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state`) are untouched by this migration (verified by snapshotting their schemas before/after).

*Delivery:* **Epic 12.**

### FR-24 Operator-facing service editing surface (slash + NL)

- **Path A — slash command:** `/service add|edit|remove|list <name> [key=value …]`. Keys: `duration` (minutes), `days` (e.g. `mon-sat`), `hours` (e.g. `10:00-19:00`), `price` (free text), `desc` (free text), `tags` (comma list). Catalog-only entries omit scheduling keys. The existing `/calendar_service` command remains as a deprecation-logged alias until the **Epic 13 cleanup PR (no later than 60 days after Epic 12 merge)**; deprecated invocations log `deprecation_warning_calendar_service` AND the bot DMs a one-time user-facing migration hint: "Команда `/calendar_service` устарела — используйте `/service` или просто напишите 'добавь услугу …'".
- **Path B — Russian natural-language dialog** (mirrors the existing `nl_knowledge_ops` / `admin_nl_dialog` pattern):
  - New api module `services/api/app/services_nl_ops.py` with `ServicesNlOpsRepository` (state machine: `pending_confirmation → confirmed | cancelled | expired`; TTL 600s; `confirm_token = secrets.token_urlsafe(16)`; atomic `consume` via `hmac.compare_digest`). New table `services_nl_op_sessions` (shape mirrors `admin_nl_op_sessions`).
  - New api endpoints (behind `internal_service_token` auth): `POST /api/projects/{project_id}/services/nl-ops`, `POST /api/projects/{project_id}/services/nl-ops/{session_id}/confirm`, `POST /api/projects/{project_id}/services/nl-ops/{session_id}/cancel`, `GET /api/projects/{project_id}/services/nl-ops/latest-pending`.
  - New bot module `services/bot_gateway/app/services_nl_dialog.py`. Keyword triggers (**start-of-message anchored**, regex `^\s*(добавь|добавьте|новая|создай|удали|измени)\s+услугу\b`): `добавь услугу`, `добавьте услугу`, `новая услуга`, `создай услугу`, `удали услугу`, `измени услугу`. On match → propose → bot DMs a Russian preview such as "Создать услугу «маникюр» (60 мин, пн–сб 10:00–19:00, цена от 2000 ₽). Подтвердите ответом «да» или /confirm <token>. Отмена: «нет» или /cancel." On `да` / `/confirm` → confirm → apply via `ProjectServiceRepository.upsert`.
  - Extraction is **regex-based** (no LLM). Ambiguous input fails closed: the bot replies "не понял, уточните". LLM-based extraction from free Russian text is a future epic.
- **Preview-rendering & threat-model rules:**
  - The Russian preview DM is rendered as **plain text** (no Telegram MarkdownV2 / HTML parse mode). Operator-supplied content (name, description, etc.) is escaped/quoted as plain text and each field is length-capped at 200 characters before rendering; longer values are truncated with a visible `…` and the preview includes the full untruncated form in a code-fenced echo so the operator can verify what is about to be applied.
  - The confirm endpoint verifies `session.originating_operator == current_sender` before accepting `confirm_token`. Cross-operator replay (operator B presents operator A's token) returns 403 `not_session_owner`.
  - **At most ONE pending session per `(project_id, operator)`** at any time. A second `добавь услугу …` trigger from the same operator on the same project while one is already pending **CANCELS the prior pending session** (status → `cancelled`) and starts a new one; the bot DMs the operator: "ваш предыдущий запрос отменён".
- **Authorization & permission split (analogous to FR-18/FR-21):**
  - Both paths gate on the project's operator registry (Epic 10 `operators` table; the sender must be a registered operator on the project). Non-registered senders are ignored silently with logged reason `unauthorized_services` — **no DM** is sent (avoids "trigger matched, silent reply" customer confusion when an operator accidentally triggers in a customer thread or when a non-operator types a trigger phrase).
  - **`/service add` and `/service edit` are operator-AND-admin** (non-destructive; analogous to enable/disable in Epic 11). Admin must also be a registered project operator (narrower than FR-21's plain admin gate — see decision-log rationale: services are project-content, not platform-level config).
  - **`/service remove` is operator-only** (destructive — irrecoverable loss of operator-curated price/description text; analogous to disconnect in FR-18). An admin attempting `/service remove` is rejected with 403 `admin_cannot_remove_service`.
  - Edit / remove **target resolution**: name must resolve to exactly one row (via the FR-23 `(project_id, lower(name))` uniqueness constraint). A no-match returns "услуга «X» не найдена". An ambiguous match cannot occur because of the uniqueness constraint, but if encountered (data drift) it fails closed with "не понял, уточните".
- **Audit:** every successful confirm logs `services_nl_op_confirmed` with the **full payload** (`trace_id, project_id, operator, op_type, name, description, price_text, tags, duration_minutes, working_hours_json, service_days_json, date_exceptions_json`). Operator-published service content is **non-secret** (it is the customer-facing price/description the bot reads back to every customer who asks), and durable values are required to answer the audit question "who set X's price to Y on date Z?". Same audit posture as today's `answer_traces`. `services_nl_op_cancelled` and `services_nl_op_expired` events carry the same full payload. **Service content is explicitly NOT subject to the FR-18 / NFR-3 secret-redaction rule** — that rule remains scoped to OAuth tokens / encryption keys.
- **Session retention:** `services_nl_op_sessions` rows are **soft-deleted on confirm/cancel/expire** (status flipped; payload retained 30 days for audit) rather than hard-deleted. Expired sessions are reaped lazily on next `latest-pending` fetch for that `(project, operator)`.

Acceptance criteria:

- Slash and NL paths converge on the same `ProjectServiceRepository.upsert` (the same DB state results regardless of input path).
- A non-registered sender's `/service` or `добавь услугу …` triggers nothing (no session row, no token, no DM); the attempt is logged as `unauthorized_services`.
- An admin who is a registered project operator can `/service add` and `/service edit` but `/service remove` returns 403 `admin_cannot_remove_service`. A pure operator can do all three.
- NL session: the `confirm_token` is single-use, expires after 600s, replay is rejected with 401/410; a token presented by a sender other than the session's originating operator returns 403 `not_session_owner`.
- A second `добавь услугу …` from the same `(project, operator)` while one is pending cancels the prior session and DMs the migration message; the bot proceeds with the new preview.
- **Russian regex "must parse" examples (all extract correctly):**
  - `добавь услугу маникюр на 60 минут пн-сб 10-19 цена 2000 описание: классический и аппаратный` → name=`маникюр`, duration=60, days=`mon..sat`, hours=10:00–19:00, price=`2000`, desc=`классический и аппаратный`.
  - `новая услуга стрижка детская длительность 30 мин цена 1500` → name=`стрижка детская`, duration=30, price=`1500`.
  - Cyrillic dash variants `пн–сб` (en-dash) / `пн-сб` (hyphen) / `пн—сб` (em-dash) all normalize identically. "ё" vs "е" normalizes identically via `RussianNormalizer` (for free, since lemmas are used at name-resolution time).
- **Russian regex "must fail closed" examples:**
  - `добавь услугу маникюр и педикюр` (two services in one utterance) → "не понял, добавьте по одной услуге за раз".
  - `добавь услугу маникюр на полтора часа` (non-digit duration) → "укажите длительность числом в минутах".
- All quoted Russian strings are **illustrative**; actual copy is configured as data files (per the Russian-first-content-is-DATA rule).

*Delivery:* **Epic 12.**

### FR-25 Catalog answer reads structured services first (humanistic, question-tailored)

- `GroundedRagAnswerer`'s catalog-query branch reads `project_services` for `ctx.project_id`. **Rendering happens at the repository boundary as natural Russian prose**, NOT as labelled `Название:` / `Цена:` blocks. Per-service format: `"Маникюр — 60 минут, пн–сб 10:00–19:00, цена от 2000 ₽. Классический и аппаратный."` (skip empty fields cleanly; no field-label tokens leak into the LLM input). Working hours, service days, and date exceptions are converted from their JSON shapes (per FR-23) via the `data/russian_calendar_terms.json` map; multi-window-per-day renders as `"пн 10:00–13:00, 14:00–19:00"`; date exceptions render as `"закрыто: 1 января, 9 мая"`. The rendered chunk is passed to the existing `answer_grounded` LLM step (no extra LLM call relative to today's digest path).
- The `grounding_system` prompt is extended with a Russian guidance rule: "Если клиент просто спрашивает, какие есть услуги — перечисли только названия, естественно и кратко. Если клиент спрашивает про цену, детали, описание или конкретную услугу — добавь только то, что он спросил. Не дампи всё подряд." The model answers tailored to the question. (Soft nudge layered on top of the hard structural guarantee: because rendering already strips field-labels, the model cannot leak `Название:` etc. even under terse prompts.)
- **Merge with digest, deduplicated.** When `project_services` is non-empty AND `_catalog_digest.get_digest(...)` returns content, both sources are combined into a single grounding chunk **with deduplication**: any service that appears in BOTH (matched by lemma-equality of the structured row's name against token sequences in the digest text, using `RussianNormalizer.lemmas`) is represented ONCE using the structured row's data (the structured row is authoritative). Digest content contributes only services not already represented as structured rows. When in doubt, both are kept — over-include is safer than under-include. The merged chunk's trace `source_id` is `merged:<project_id>`.
- If `project_services` is empty for the project → fall back to the full existing `_catalog_digest.get_digest(...)` LLM path (`source_id` `catalog_digest:<project_id>`), so projects that only ever used `/kb_add` PDF uploads continue to work.
- If only `project_services` has content and the digest is empty → render structured only (`source_id` `project_services:<project_id>`).
- If both `project_services` and the digest are empty → existing `_skip(reason='catalog_empty')` behavior.
- **Guardrails audit (release-readiness):** before merge, `data/russian_hedges.txt` is audited against typical price/duration phrasings (e.g. "от 2000 ₽", "от 60 минут") so the existing verifier does not false-reject legitimate catalog answers as "hedging."

Acceptance criteria:

- **No label leak:** the customer-visible answer for any catalog question contains none of the field-label substrings `Название:`, `Описание:`, `Цена:`, `Длительность:`, `Дни:`, `Часы:` (verified by an asserting test).
- **General services question:** for "какие услуги?" on a 3-service project, the response includes the names of all 3 services and **NO `price_text` and NO `description` fields** unless the customer explicitly asks for them.
- **Single-service question, bounded surface:** for "сколько стоит маникюр?", the response includes at most **one service's `price_text` and at most one service's `description`** (the resolved service); no other services' prices or descriptions appear in the answer.
- **Trace source-id literal:** the `answer_traces.source_id` for the catalog branch carries exactly one of `project_services:<project_id>` (structured-only), `catalog_digest:<project_id>` (digest-only fallback), or `merged:<project_id>` (both sources merged) — per the four branches above.
- **Brownfield continuity:** for a project that has only ever used `/kb_add` PDF uploads (empty `project_services`), the catalog answer continues to return the same content profile as before Epic 12 (digest-only path, `source_id` `catalog_digest:<project_id>`).
- **Single-row insert does NOT silently shrink the catalog:** after adding one service row to a project that already has a 12-service digest, "какие услуги?" still returns up to 12 services (merged + deduplicated), not just the one structured row; the new `source_id` is `merged:<project_id>`.

*Delivery:* **Epic 12.**

## 5. Non-Functional Requirements (NFR)

### NFR-1 Reliability

- System supports graceful degradation during provider/dependency incidents.

### NFR-2 Observability

- Operational metrics and structured logs are available for debugging and incident triage.

### NFR-3 Security

- Secrets are environment-managed and not committed.
- Admin actions are auditable.
- **OAuth & per-operator credentials (Epic 11):** the Google OAuth *client* secret, redirect URI, and the token-encryption key are environment-managed (never committed). Per-operator OAuth **refresh tokens** are user-scoped credentials obtained via 3-legged consent and stored **encrypted at rest** in a dedicated SQLite store (Fernet/AES; key from env) — never in environment variables, never in logs, never in answer-trace metadata. Calendar consent is scoped read-only. Operator connect/disconnect events are auditable. A leaked refresh token equates to standing calendar access, so encryption-at-rest and read-only scope are mandatory mitigations.

### NFR-4 Performance

- MVP response latency target and throughput thresholds must be defined and validated.

### NFR-5 Maintainability

- Service boundaries and interfaces are explicit to support incremental evolution.

### NFR-6 Deployability

- DigitalOcean deployment path is documented and reproducible with Docker-first assumptions.

### NFR-7 Recoverability

- Retrieval store supports routine backup and controlled restore with defined operational RPO/RTO targets.

## 6. Data Requirements

Persistence is SQLite, one DB file per concern under `.data/`. All access is via
`*Repository` classes. Core stores and their primary tables:

| DB file | Primary tables |
|---------|----------------|
| `semantaix_story1.db` | `conversations`, `messages` |
| `semantaix_hitl.db` | `hitl_tickets`, `hitl_runtime_config`, `project_prompts`, `project_prompt_versions`, `pending_prompt_edits` |
| `semantaix_incidents.db` | `incidents`, `incident_events` |
| `semantaix_knowledge.db` | `knowledge_candidates`, `knowledge_moderation_candidates` |
| `semantaix_rag.db` | `rag_chunks`, `catalog_digests` |
| `semantaix_answer_traces.db` | `answer_traces` (append-only transparency records; `answer_traces.source_id` for the catalog branch carries one of `project_services:<project_id>` (structured-only), `catalog_digest:<project_id>` (digest-only fallback), or `merged:<project_id>` (both sources merged) per FR-25) |
| `semantaix_nl_ops.db` | `nl_op_sessions`, `admin_nl_op_sessions`, `services_nl_op_sessions` (operator NL services dialog sessions, Epic 12; TTL 600s; status enum `pending_confirmation → confirmed | cancelled | expired`; soft-deleted rows retained 30 days for audit; `payload_json` blob holds operator-typed structured intent and is preserved through soft-delete), `nl_audit_logs`, `knowledge_versions`, `trace_corrections` |
| `semantaix_operator_files.db` | `operator_files`, `operator_kb_session`, `operator_media_group_buffer` |
| `semantaix_projects.db` | `projects` |
| `semantaix_operators.db` | `operators` |
| `semantaix_web_auth.db` | `web_auth_codes`, `web_sessions` |
| `semantaix_admin_sessions.db` | `admin_login_codes`, `admin_sessions` |
| `semantaix_backups.db` | `backups`, `backup_events` |
| `semantaix_calendar.db` (Epic 11) | `calendar_project_settings` (enablement, designated calendar operator, project timezone, freeBusy look-ahead), `calendar_operator_tokens` (Fernet-encrypted refresh tokens, upsert-keyed by project+operator), `calendar_oauth_pending_state` (single-use `state` with TTL) |
| `semantaix_calendar.db` (Epic 12) | `project_services` (canonical project services catalog: `id, project_id, name, description, price_text, tags_json, duration_minutes, working_hours_json, service_days_json, date_exceptions_json, updated_at` — renamed from `calendar_service_rules`; `UNIQUE(project_id, lower(name))`; calendar-eligible iff `duration_minutes IS NOT NULL`; JSON shapes pinned in FR-23) |

Runtime configuration (operator routing, ack message, locale, grounding threshold,
bot persona) lives in `hitl_runtime_config` rather than a separate `system_settings`
table. Audit evidence for knowledge mutations and corrections lives in
`nl_audit_logs`.

## 7. Success Metrics

- AI deflection rate.
- Escalation completion rate.
- Incident mean-time-to-acknowledge.
- Retrieval hit-rate/groundedness on golden set.
- Candidate-to-approved knowledge conversion rate.
- (Calendar, Epic 11) Availability-answer rate vs escalation on calendar-enabled projects (target: majority of resolved-service availability questions answered directly rather than escalated; exact threshold set after a baseline period).
- (Calendar, Epic 11) Operator calendar-connect success rate; reconnect frequency (a proxy for token-expiry pain).
- (Calendar, Epic 11) Counter-metric: rate of incorrect availability answers (target ≈ 0; a wrong "free" is worse than an escalation).

## 8. Risks and Mitigations

- Noisy extraction degrades RAG quality -> mandatory moderation gate.
- Alert fatigue -> strict dedup/throttle and severity policy.
- Provider instability -> resilience layer + fallback behavior.
- Operational blind spots -> enforced health, logs, incident workflows.
- (Calendar) OAuth refresh-token leakage -> encryption at rest + read-only consent scope + tokens never logged.
- (Calendar) Wrong availability answer -> escalate-on-uncertainty, service-rule gating on top of free/busy, never echo event content.
- (Calendar) Timezone/DST errors -> tz-aware datetimes via `zoneinfo`, compare in UTC, config-driven project timezone (customer times interpreted in project tz).
- (Calendar) **Google OAuth app verification** for the sensitive `calendar.readonly` scope is an external, multi-week dependency -> plan verification ahead of GA; operate within Google's test-user allowlist during pilot; track as a release-readiness gate (§9).
- (Calendar) **Refresh-token long-term expiry** (7-day "Testing"-status / 6-month-unused / token-cap) silently disconnects operators -> detect on next use, notify operator to reconnect, emit incident, clear dead token.

## 9. Release Readiness Criteria (MVP)

- All P0 flows pass automated tests and manual verification runbook.
- Docker compose stack operational with health checks.
- Alerts tab and Telegram critical notifications validated.
- HITL round-trip verified end-to-end.
- Moderation to reindex loop verified on sample data.
- Guardrail decision logic verified for valid/invalid branches.
- Backup/restore flow verified with visible last-backup timestamp and storage location in UI.
- (Calendar, Epic 11) Google OAuth app passes verification for the `calendar.readonly` scope — or the pilot operates within Google's documented test-user limits — before calendar GA.
- (Calendar, Epic 11) Operator connect → availability round-trip verified end-to-end (connect → freeBusy → availability answer), including the disconnect/reconnect and revoked-token paths.

## 10. Delivery Mapping (Current Backlog)

This PRD maps directly to the existing ordered Linear execution sequence (`FLE-5` to `FLE-18`) under project `semantaix`.

Post-MVP tenant capabilities (**FR-15–FR-17**) are planned in **Epic 08** and assume completion of Epics **03** (guardrail payload), **05** (RAG lineage), **06** (moderation/reindex), and **02** (incidents)—see `epics/epic-08-tenant-knowledge-ops-and-answer-traces.md`.

Calendar availability & scheduling (**FR-18–FR-22**) is planned as **Epic 11** (read-only first). It builds on the answer pipeline (Epics 01/03), project & multi-operator scoping (Epics 08/10), incident integration (Epic 02), and the Telegram operator-command surface (Epic 09)—see `epics/epic-11-calendar-availability-scheduling.md` (to be created by `bmad-create-epics-and-stories`).

Unified Project Services Catalog (**FR-23–FR-25**) is planned as **Epic 12** (one canonical structured services table powering both the catalog answer and the calendar). It depends on Epic 11 (table rename target + calendar reads), Epic 10 (operator registry for authorization), Epic 09 (operator command surface), and the existing `_catalog_digest` / `GroundedRagAnswerer` plumbing — see `epics/epic-12-unified-project-services-catalog.md` (to be created by `bmad-create-epics-and-stories`).

## 11. Glossary

Load-bearing nouns, disambiguated for downstream UX/architecture/story work:

- **Service (microservice):** one of the five FastAPI runtime services (`api`, `web_ui`, `bot_gateway`, `ingest_worker`, `scheduler`). Used in §1, NFR, architecture.
- **Project:** a tenant-scoped configuration boundary delivered by Epics 08/10; owns its knowledge, operators, runtime config, and (optionally) calendar settings.
- **Project service (Epic 12):** a canonical operator-curated row in `project_services` per project, carrying `name` (required) plus optional description / price / tags AND optional scheduling fields. The same row may appear in the catalog answer AND be schedulable on the calendar. **Catalog-eligible always; calendar-eligible iff `duration_minutes IS NOT NULL`.** See FR-23 / FR-20 / FR-22.
- **Schedulable service (Epic 12):** the calendar-eligible subset of project services — rows where `duration_minutes IS NOT NULL`; consumed by `compute_availability` and the calendar `service_resolver`. This is the sense used in **FR-19/FR-20/FR-22**. (Avoid the word "bookable" — booking/write is out of scope this phase.)
- **Operator:** a human who answers escalations and owns project assets (uploads, `/kb_add`, and now calendar connection). Identified by Telegram username.
- **Calendar operator (Epic 11):** the single operator a calendar-enabled project designates as the source of availability (v1; multi-operator selection deferred).
- **Tenant:** synonym for the project boundary in older PRD text (e.g. FR-15 "Tenant-Scoped"); "project" is the current term.