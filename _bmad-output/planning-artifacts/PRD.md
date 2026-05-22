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
- Access tokens are minted on demand and cached until near-expiry.
- **Revocation & long-term expiry handling:** a refresh that fails — operator revoked access on Google, or the refresh token expired (Google's 7-day "Testing"-status, 6-month-unused, or per-client token-cap rules) — is detected on next use. The operator transitions to a **"reconnect needed"** state, is proactively notified via Telegram to re-run `/connect_calendar`, an **incident is emitted** (Epic-02 integration), and the dead token row is **cleared** (never left as a poison row). No customer-visible error.
- Disconnect (**operator-only**): best-effort call to Google's token-revocation endpoint, then delete the local token regardless (if revoke fails, still delete locally and log). Connecting and disconnecting are auditable operator actions; an admin cannot disconnect (admins may only enable/disable per FR-21).

Acceptance criteria:

- Successful consent → stored encrypted refresh token + Telegram confirmation. A forged, expired, replayed, or unmatched `state` is rejected and nothing is stored.
- `state` is single-use (consumed on first callback) and expires after its TTL.
- A revoked/expired refresh token is detected on next use → reconnect state + operator notification + incident emitted + token cleared, with no customer-visible error.
- Re-connecting the same operator overwrites the prior token (one row per `(project, operator)`).
- Tokens, secrets, and the encryption key never appear in logs or answer-trace metadata.

*Delivery:* **Epic 11.**

### FR-19 Calendar Availability Answering

- For a calendar-enabled project, when a customer's question resolves to a configured service (**FR-22**), the system makes one live `freeBusy` call against the calendar operator's **primary** Google calendar over the look-ahead window, **intersects it with the per-service rules of FR-20**, interprets all times in the **project timezone**, and answers in Russian.
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

- Each calendar-enabled project defines its **schedulable** services with rules: service name (resolved per FR-22), duration, working-hours windows (**one or more per day**, e.g. to model a lunch break), recurring **service-days** (days of week), and **date-level exceptions/closures** (honoring RU public holidays via the existing `holidays` library).
- Rules are runtime configuration (the `hitl_runtime_config` config-in-DB pattern), editable without code changes, scoped per project; in v1 all services map to the single project calendar operator.

Acceptance criteria:

- Availability answers (FR-19) honor the configured duration, working-hours windows, service-days, and date exceptions/holiday closures, evaluated in the project timezone.
- Changing a service rule changes subsequent availability answers without redeploy.

*Delivery:* **Epic 11.**

### FR-21 Per-Project Opt-In Gating

- The calendar capability is **default-off**; a project must explicitly enable it **and** designate a calendar operator.
- The answer pipeline treats calendar as a tri-state: **(a) not enabled** → silent no-op (the calendar logic declines, the pipeline proceeds normally, no error); **(b) enabled but the calendar operator is not connected / in reconnect state** → a "calendar isn't connected yet" reply and/or HITL escalation, never a 500; **(c) connected** → compute and answer per FR-19.
- **Enable/disable vs disconnect (permission model):** "**Disable**" turns the feature off for a project but **keeps** the stored token; "**disconnect/delete**" removes the integration and deletes the stored token (FR-18). Both the **calendar operator** and an **admin** may enable/disable a project's calendar. **Only the operator may disconnect/delete** the integration — an admin can turn it off but cannot delete the operator's connected calendar.
- The enablement check is a **single cached project-settings read performed before intent detection or any API call**; its overhead is negligible. (The exact pipeline placement — a standalone answerer vs a `scheduling_context` signal — is decided in the architecture step, but the "config check precedes intent/API work" ordering is the binding requirement.)

Acceptance criteria:

- On a project with calendar disabled, calendar logic adds no customer-visible behavior, and the project-settings check precedes intent detection and any API call.
- Enabling a project and connecting its calendar operator makes availability answers live; disabling reverts to the no-op state without deleting the stored token.
- The operator and an admin can both enable/disable; an admin attempting to disconnect/delete the integration is rejected (operator-only).

*Delivery:* **Epic 11.**

### FR-22 Service Resolution from Russian Text

- Map a customer's free Russian text to a configured service via **lemma matching** (the existing `RussianNormalizer`), not raw string equality.
- **No match** (named service isn't configured) or **date/time given but no service named** → ask **one** clarifying question; if still unresolved → escalate to HITL.
- **Ambiguous match** (multiple services match) → ask **one** disambiguating question; if still ambiguous → escalate to HITL.
- The system never guesses a service.

Acceptance criteria:

- A lemma match to exactly one configured service resolves to that service.
- No-match / ambiguous-match / no-service-named triggers exactly **one** clarifying turn before escalation; unresolved after that clarification escalates (never silently picks a service).

*Delivery:* **Epic 11.**

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
| `semantaix_answer_traces.db` | `answer_traces` (append-only transparency records) |
| `semantaix_nl_ops.db` | `nl_op_sessions`, `admin_nl_op_sessions`, `nl_audit_logs`, `knowledge_versions`, `trace_corrections` |
| `semantaix_operator_files.db` | `operator_files`, `operator_kb_session`, `operator_media_group_buffer` |
| `semantaix_projects.db` | `projects` |
| `semantaix_operators.db` | `operators` |
| `semantaix_web_auth.db` | `web_auth_codes`, `web_sessions` |
| `semantaix_admin_sessions.db` | `admin_login_codes`, `admin_sessions` |
| `semantaix_backups.db` | `backups`, `backup_events` |
| `semantaix_calendar.db` (Epic 11) | `calendar_project_settings` (enablement, designated calendar operator, project timezone, freeBusy look-ahead), `calendar_operator_tokens` (Fernet-encrypted refresh tokens, upsert-keyed by project+operator), `calendar_oauth_pending_state` (single-use `state` with TTL), `calendar_service_rules` (duration, working-hours windows, service-days, date exceptions) |

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

## 11. Glossary

Load-bearing nouns, disambiguated for downstream UX/architecture/story work:

- **Service (microservice):** one of the five FastAPI runtime services (`api`, `web_ui`, `bot_gateway`, `ingest_worker`, `scheduler`). Used in §1, NFR, architecture.
- **Schedulable service / offering:** a bookable offering a project exposes for availability questions (e.g. "маникюр"), defined in `calendar_service_rules`. This is the sense used in **FR-19/FR-20/FR-22**. (Avoid the word "bookable" — booking/write is out of scope this phase.)
- **Project:** a tenant-scoped configuration boundary delivered by Epics 08/10; owns its knowledge, operators, runtime config, and (optionally) calendar settings.
- **Operator:** a human who answers escalations and owns project assets (uploads, `/kb_add`, and now calendar connection). Identified by Telegram username.
- **Calendar operator (Epic 11):** the single operator a calendar-enabled project designates as the source of availability (v1; multi-operator selection deferred).
- **Tenant:** synonym for the project boundary in older PRD text (e.g. FR-15 "Tenant-Scoped"); "project" is the current term.