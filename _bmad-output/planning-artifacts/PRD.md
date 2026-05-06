# Semantaix PRD

## 1. Product Overview

Semantaix is a Telegram-based AI assistant for customer support/sales that uses RAG to answer questions, escalates uncertain requests to a human operator, and continuously improves knowledge through moderation workflows.

This PRD is scoped to the confirmed Option B implementation strategy:

- FastAPI-centered architecture
- Docker-first deployment model
- PostgreSQL + Qdrant
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

### 2.2 Non-Goals (MVP)

- Multi-tenant architecture.
- Advanced BI analytics dashboards.
- Complex enterprise RBAC beyond practical admin/operator needs.
- Cross-channel support beyond Telegram.

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

- System retrieves relevant context from Qdrant and composes response prompt.
- Responses must be grounded in retrieved content when available.

Acceptance criteria:

- Retrieval pipeline logs top context candidates with trace ID.
- Guardrail policy enforces fallback when grounding/confidence is below threshold.

### FR-3 Human-in-the-Loop Escalation

- If AI cannot answer confidently, a durable escalation ticket is created.
- Escalation routed to configurable primary Telegram username.
- Operator response is mapped back to originating user and delivered as a bot-authored message.

Acceptance criteria:

- Escalation ticket lifecycle states are persisted (`open`, `claimed`, `answered`, `closed`).
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

- All conversation messages are stored in PostgreSQL.
- Separate extraction pipeline generates `knowledge_candidates` from useful snippets only.
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
- Notification types include:
  - provider 429 spikes
  - provider 5xx spikes
  - vector DB down
  - Postgres down
  - dead-letter queue growth
  - HITL delivery failures

Acceptance criteria:

- Alerts are deduplicated/throttled by policy window.
- Delivery status is recorded in incident event history.

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

### FR-14 Qdrant Backup and Restore Operations

- System performs regular Qdrant backups.
- Web UI shows backup list, last successful backup timestamp, and storage location.
- Web UI provides restore action with confirmation and status reporting.

Acceptance criteria:

- Scheduled backup runs persist metadata in DB.
- Last backup timestamp and location are visible in UI.
- Restore operation is auditable and reports success/failure events.

### FR-15 Tenant-Scoped Answer Transparency (“Why This Answer”)

- For delivered or policy-blocked AI paths tied to a stored end-user message, the system persists a queryable **answer trace** capturing retrieval lineage (chunk references and scores), guardrail/policy outcome, model routing metadata, and an MVP grounding/confidence snapshot.
- Tenant-authorized Web UI users can open a conversation message and view that trace (read-only).

Acceptance criteria:

- Trace records are durable, tenant-scoped, and **append-only** (corrections create new knowledge versions; they do not rewrite historical traces).
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

## 5. Non-Functional Requirements (NFR)

### NFR-1 Reliability

- System supports graceful degradation during provider/dependency incidents.

### NFR-2 Observability

- Operational metrics and structured logs are available for debugging and incident triage.

### NFR-3 Security

- Secrets are environment-managed and not committed.
- Admin actions are auditable.

### NFR-4 Performance

- MVP response latency target and throughput thresholds must be defined and validated.

### NFR-5 Maintainability

- Service boundaries and interfaces are explicit to support incremental evolution.

### NFR-6 Deployability

- DigitalOcean deployment path is documented and reproducible with Docker-first assumptions.

### NFR-7 Recoverability

- Retrieval store supports routine backup and controlled restore with defined operational RPO/RTO targets.

## 6. Data Requirements

- Core tables include:
  - users
  - conversations
  - messages
  - escalation_tickets
  - incidents
  - incident_events
  - vector_backups
  - system_settings
  - knowledge_items
  - knowledge_versions
  - knowledge_candidates
  - audit_logs
  - answer_traces (Epic 08; tenant-scoped, append-only transparency records)

## 7. Success Metrics

- AI deflection rate.
- Escalation completion rate.
- Incident mean-time-to-acknowledge.
- Retrieval hit-rate/groundedness on golden set.
- Candidate-to-approved knowledge conversion rate.

## 8. Risks and Mitigations

- Noisy extraction degrades RAG quality -> mandatory moderation gate.
- Alert fatigue -> strict dedup/throttle and severity policy.
- Provider instability -> resilience layer + fallback behavior.
- Operational blind spots -> enforced health, logs, incident workflows.

## 9. Release Readiness Criteria (MVP)

- All P0 flows pass automated tests and manual verification runbook.
- Docker compose stack operational with health checks.
- Alerts tab and Telegram critical notifications validated.
- HITL round-trip verified end-to-end.
- Moderation to reindex loop verified on sample data.
- Guardrail decision logic verified for valid/invalid branches.
- Backup/restore flow verified with visible last-backup timestamp and storage location in UI.

## 10. Delivery Mapping (Current Backlog)

This PRD maps directly to the existing ordered Linear execution sequence (`FLE-5` to `FLE-18`) under project `semantaix`.

Post-MVP tenant capabilities (**FR-15–FR-17**) are planned in **Epic 08** and assume completion of Epics **03** (guardrail payload), **05** (RAG lineage), **06** (moderation/reindex), and **02** (incidents)—see `epics/epic-08-tenant-knowledge-ops-and-answer-traces.md`.