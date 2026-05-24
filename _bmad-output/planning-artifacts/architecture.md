# Semantaix Architecture (As-Built)

> This document reflects the system **as implemented**. Where the original Option B
> plan assumed PostgreSQL + Qdrant-vector retrieval, the MVP shipped on **SQLite**
> (one DB file per concern) with **lemma-overlap retrieval**. Postgres and Qdrant
> remain provisioned in compose but are not on the runtime data path (see
> [Provisioned-but-unused](#provisioned-but-unused-infrastructure)).

## Stack and Services

Docker-first microservices: five FastAPI services behind an nginx reverse proxy.

| Service | Port | Role |
|---------|------|------|
| `api` | 8000 | Core business logic for all epics (conversations, RAG, HITL, incidents, knowledge, traces, admin) |
| `web_ui` | 8001 | Admin/operator shell UI (alerts, knowledge moderation, "Why this answer", files, projects/operators) |
| `bot_gateway` | 8002 | Telegram webhook intake + outbound messaging orchestration |
| `ingest_worker` | 8003 | Heartbeat placeholder |
| `scheduler` | 8004 | Heartbeat placeholder |

**Reverse proxy (nginx, port 80):**

| Path | Target |
|------|--------|
| `/api/` | `api:8000` |
| `/admin/` | `web_ui:8001` |
| `/telegram/webhook` | `bot_gateway:8002/telegram/webhook` |
| `/health/live` | served locally (200) |

Every service exposes `/health/live`, `/health/ready`, `/health/startup` via
`platform_common/app_factory.py`. Settings are centralized in a single
Pydantic `Settings` class (`platform_common/settings.py`) shared across services.

## Data Stores (SQLite, system of record)

Each concern owns its own SQLite file under `.data/`. There is no relational
RDBMS in the runtime path; SQLite files are the durable system of record.

| DB file | Owner | Tables (primary) |
|---------|-------|------------------|
| `semantaix_story1.db` | bot_gateway | `conversations`, `messages` |
| `semantaix_operator_files.db` | bot_gateway (RO cross-read by api) | `operator_files`, `operator_kb_session`, `operator_media_group_buffer` |
| `semantaix_hitl.db` | api | `hitl_tickets`, `hitl_runtime_config`, `project_prompts`, `project_prompt_versions`, `pending_prompt_edits` |
| `semantaix_knowledge.db` | api | `knowledge_candidates`, `knowledge_moderation_candidates` |
| `semantaix_rag.db` | api | `rag_chunks`, `catalog_digests` |
| `semantaix_answer_traces.db` | api | `answer_traces` |
| `semantaix_incidents.db` | api | `incidents`, `incident_events` |
| `semantaix_nl_ops.db` | api | `nl_op_sessions`, `admin_nl_op_sessions`, `nl_audit_logs`, `knowledge_versions`, `trace_corrections` |
| `semantaix_web_auth.db` | api | `web_auth_codes`, `web_sessions` |
| `semantaix_projects.db` | api | `projects` |
| `semantaix_operators.db` | api | `operators` |
| `semantaix_admin_sessions.db` | api | `admin_login_codes`, `admin_sessions` |
| `semantaix_backups.db` | api | `backups`, `backup_events` |

**WAL mode** is enabled on `semantaix_operator_files.db`,
`semantaix_knowledge.db`, and `semantaix_web_auth.db` so the api service can open
them read-only (and `ATTACH` the knowledge DB to the operator-files DB in a single
query) while the owning service writes. All DB access is funneled through
`*Repository` classes — no raw SQL outside repositories. Trace lineage uses stable
`rag_chunks` ids (no external vector store id needed).

## Conversation / Answer Pipeline (api)

`POST /conversations/inbound` is the single entry point for every customer
message. It builds an `AnswerContext` and runs an `AnswerPipeline`
(`services/api/app/answerers/`). As built, the pipeline is a **single
`GroundedRagAnswerer`** that internally folds in the capabilities that earlier
plans split across separate answerers:

- **Scheduling / date-time / holiday** intent (`scheduling_context.py`)
- **Weather** lookups (`weather_client.py`, Open-Meteo)
- **Service-catalog** intent (`service_catalog_intent.py`)
- **Grounded RAG** answering with a four-layer validity gate:
  1. **Retrieve** — lemma-overlap retrieval over `rag_chunks` (grounding threshold default `0.6`)
  2. **Strict-grounding LLM** — emits the `ESCALATE_TO_HUMAN` sentinel when it cannot answer from context
  3. **LLM verifier** — must return `GROUNDED`
  4. **Regex guardrails + profanity** — `guardrails.py` (0.95 valid / 0.2 blocked) and Russian profanity filter

If no layer produces a deliverable answer, the request **escalates to HITL**:
ack the customer, coalesce onto an active ticket (or create + assign one), and DM
the operator the verbatim question. The LLM is never user-visible unless it clears
all four grounding layers.

## RAG (api)

- **Ingest** (`/rag/ingest`, `rag.py`): line-split into chunks, SHA-256 dedup
  (`UNIQUE(source_id, chunk_hash)`), optional `is_confidential` / `project_id`.
- **Retrieve** (`/rag/retrieve`): **lemma-overlap scoring**, not vector search.
  `RussianNormalizer.lemmas` (razdel + slang dict + pymorphy3) tokenizes query and
  chunks; score = matched-lemma overlap with retrieval-stopword discounting. No
  embedding model and no Qdrant call are on this path.

## HITL Escalation (api + bot_gateway)

- Tickets persist in `semantaix_hitl.db`. Lifecycle is **`open` → `assigned` →
  `resolved`** (operator reply auto-resolves).
- Runtime routing config lives in `hitl_runtime_config` with **DB-first / `.env`
  fallback** precedence (operator username + chat id, ack message, country/timezone/
  location, grounding threshold, bot persona).
- `bot_gateway` branches inbound messages: customer → `/conversations/inbound`;
  operator (matches configured operator username) → resolve ticket id from
  `reply_to_message` (or the single open assigned ticket) → `/hitl/tickets/{id}/reply`.
- Admin command `/hitl_config @username <chat_id>` upserts runtime config; gated by
  the configured admin username. Outbound delivery is bot-authored — no operator
  metadata leaks to the end user.

## Incidents (api)

`incidents.py`: fingerprint-based dedup window (default 300 s), status lifecycle
with an `incident_events` timeline (`created`, `deduplicated`, `auto_resolved`,
`read`, `acknowledged`, `resolved`, `telegram_notify`). Critical incidents notify
the on-call operator via Telegram. The Web UI Alerts surface reads deduplicated
records.

## Knowledge Moderation + NL Ops (api)

- **Extraction → moderation**: `/knowledge/extract` pulls transcript lines into
  `knowledge_moderation_candidates`; `/knowledge/candidates/*` approve (triggers RAG
  reindex) or reject.
- **NL knowledge ops** (`nl_knowledge_ops.py`): bot-first conversational
  create/update/retire with preview + explicit confirm token, `knowledge_versions`
  history, and `nl_audit_logs`. **Tenant-scoped** (`tenant_id` on sessions, versions,
  audit logs). Tenants can be configured to route mutations into the moderation queue
  instead of direct publish.

## Answer Traces + Correction Loop (api + web_ui)

- `answer_traces` is written at decision time (retrieval hits with scores, guardrail
  outcome/reasons/score, model routing, confidence, `hitl_ticket_id`). Records are
  **append-only**. Note: as built, traces are **global, not tenant-partitioned**.
- Web UI conversation/trace detail renders a read-only **"Why this answer"** panel.
- `trace_corrections.py`: from a trace, a tenant user submits a correction routed
  either to direct publish or the moderation queue (`trace_corrections`, tenant-scoped),
  cross-linked in `nl_audit_logs`. Past traces are never rewritten.

## Operator Files (bot_gateway + api)

Operators upload files via Telegram; `operator_files.py` registers them
(`operator_files`, WAL), stores the binary, extracts text, and ingests chunks into
RAG (with confidentiality flags). The api exposes `/admin/files`,
`/admin/files/{short_id}`, `/admin/files/search` (`admin_files.py` +
`operator_files_view.py`): admin sees all (incl. confidential), operator sees own
only — enforced in SQL `WHERE` clauses. Accepts a cookie session **or**
`Authorization: Bearer <internal_service_token>` + `as_user=` for bot→api calls.
Bot commands: `/files [N]`, `/file <short_id>`, `/files_find <query>`.

## Multi-Operator Projects + Web Auth (api + web_ui)

- `projects` and `operators` tables scope knowledge and routing; `rag_chunks` and
  candidates carry `project_id`. A default project is auto-created.
- **Web auth** (`admin_auth.py` + `web_auth.py`): Telegram one-time code login.
  `request_code` resolves chat id and DMs a 6-digit code (5-min TTL, 5-attempt cap);
  `verify` consumes it, rotates prior sessions, sets an `HttpOnly; SameSite=Lax`
  cookie (`semantaix_session`). Sessions do not expire (revoke-based). Service-to-
  service calls use the internal service token.

## Backup / Restore (api + web_ui)

`backups.py`: backups are **tar.gz archives of the SQLite DB files** (not Qdrant
snapshots). Runs are recorded in `backups` with a `backup_events` audit trail
(`backup_started/completed/failed`, `restore_completed/failed`). Restore requires a
confirmation token. The Web UI shows the latest successful backup metadata and the
restore action.

## Russian-First Text Handling (api)

`RussianNormalizer` (`russian_text/`) wraps razdel tokenization + a static slang
dictionary (`data/russian_slang.json`) + pymorphy3 lemmatization. It is the shared
seam across **retrieval** (`rag.py` `_tokenize`), **guardrails** (hedge/policy lists
in `data/russian_hedges.txt`, `data/russian_policy_phrases.txt`), and **profanity**
filtering (`data/russian_profanity.txt`). New slang pairs added to the JSON improve
retrieval, intent, and guardrails together.

## Provisioned-but-unused Infrastructure

- **Qdrant** (compose service, port 6333): provisioned and included in
  health/readiness checks (`qdrant_url`), but **no vector indexing or query** runs
  against it. Retrieval is lemma-overlap. Kept as the forward path for embedding-based
  retrieval.
- **PostgreSQL**: `database_url` defaults to a Postgres DSN in settings, and a
  `postgres` service exists in compose behind `profiles: ["with-postgres"]`
  (inactive by default). Nothing imports a Postgres driver; it is not on the runtime
  path.

The Docker-first deployment model and observability conventions (`trace_id`,
structured logs, per-service health checks) are unchanged.

## Calendar Availability & Scheduling (api + bot_gateway + web redirect) — Epic 11 (planned)

Read-only availability first (PRD **FR-18–FR-22**), opt-in per project and default-off. New `services/api/app/calendar/` package; new SQLite store `semantaix_calendar.db`. Follows the project-context rules: httpx transport, sync SQLite via `asyncio.to_thread`, injected clock + http client, per-layer failure conventions, 100% coverage, one PR per story.

**Components (`services/api/app/calendar/`):**

| Component | Type | Responsibility |
|---|---|---|
| `CalendarAvailabilityAnswerer` | Answerer (Protocol) | Orchestrates gate → intent → service-resolve → freeBusy → availability → answer/escalate. Placed **before** `GroundedRagAnswerer` in the pipeline. |
| `CalendarOAuthClient` | Client | `google-auth-oauthlib` Flow (code exchange) + `google-auth` `Credentials.refresh()`; **sync, via `asyncio.to_thread`** (google-auth owns token transport). |
| `CalendarFreeBusyClient` | Client (httpx) | `POST /freeBusy` over an **injected** `httpx.AsyncClient` with explicit timeout; returns a frozen `FreeBusy` dataclass (busy intervals only). |
| `compute_availability(...)` | Pure function | `(now, busy_blocks, service_rule, project_tz, requested_start) → AvailabilityResult`. Clock injected, tz-aware, no I/O — the 100%-coverage core; slot-fit `[start, start+duration)`. |
| `service_resolver` | Pure function | FR-22: lemma-match free Russian text → service via `RussianNormalizer`; `resolved | none | ambiguous`. |
| `CalendarTokenRepository` | Repository (sync sqlite3) | Fernet-encrypted refresh tokens, **upsert on `(project_id, operator)`**; raises `TokenNotFound` / `TokenRefreshFailed`. |
| `CalendarOAuthStateRepository` | Repository (sync sqlite3) | Single-use `state` with TTL; atomic `consume(state)`. |
| `CalendarSettingsRepository` | Repository (sync sqlite3) | Per-project enablement, calendar operator, project timezone, look-ahead; per-service rules. |

**Data store** `semantaix_calendar.db`: `calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state`, `calendar_service_rules` (see PRD §6).

**Connect flow:** operator `/connect_calendar` (bot_gateway, operator-gated, mirrors `kb_intent.py`) → api mints single-use `state` + consent URL → bot DMs URL → operator consents → Google redirects to the **api callback route** (browser-facing, public via nginx, rate-limited; `state` is the sole browser↔operator binding) → validate+consume `state` → `to_thread(Flow.fetch_token)` → encrypt+upsert token → **auto-enable**: `to_thread(CalendarSettingsRepository.enable)` flips the project to `enabled=1` and records the connecting operator as the designated calendar operator atomically with the token upsert (existing `project_timezone` / `lookahead_days` are preserved on re-connect) → HTML + Telegram confirmation. A failure in the enable write after the token upsert surfaces a 500-class error rather than a misleading success page (the operator retries by re-running `/connect_calendar`). **There is no separate `/enable` endpoint or `/calendar_on` command** — connect is the only enable path. `/calendar_off` (operator + admin) flips `enabled=0` while keeping the stored token; re-enable means re-running `/connect_calendar`.

**Availability flow:** inbound → pipeline → `CalendarAvailabilityAnswerer`: (1) **gate** — cached settings read; disabled → `handled=False`; (2) **intent** — reuse the scheduling regex; non-scheduling → `handled=False`; (3) **service resolve** (FR-22) — none/ambiguous → one clarifying turn, else escalate; (4) **token** — `to_thread(repo.get)`; missing/reconnect → "not connected"/escalate; (5) **freeBusy** — refresh under per-operator lock if near expiry, one httpx call with timeout; (6) **`compute_availability`** in project tz → Russian answer; any failure → escalate to the calendar operator.

**Resilience & rate limiting:**

- **Token expiration:** access token cached with expiry; refresh within a skew window guarded by a **per-operator `asyncio.Lock`** (single-flight). Refresh-token expiry (Google 7-day "Testing", 6-month-unused, token-cap) or revocation is caught on the failing refresh → operator → reconnect state + Telegram notice + **incident** + dead row cleared. No customer-visible error.
- **API timeouts:** explicit timeouts on the httpx `freeBusy` call and the `to_thread`-wrapped google-auth calls; a timeout is a provider error → **escalate, never guess**; repeated occurrences emit incidents.
- **Rate limiting:** *inbound* — the unauthenticated OAuth callback and `/connect_calendar` are rate-limited per operator; *outbound* — Google `429` → respect `Retry-After` with one bounded retry then escalate; volume bounded by **one `freeBusy` call per question** (no result caching in v1, also avoids stale "free").
- **Incidents (Epic-02 integration):** OAuth exchange failure, refresh failure, freeBusy provider error/timeout, 429 exhaustion.

**Key decisions:** (1) **standalone answerer** before `GroundedRagAnswerer`, not a `scheduling_context` signal (deterministic answer; cheap opt-in gate = fast `handled=False`); (2) **callback in `api`** (co-located with token store/client; avoids cross-service handoff of the auth `code`); (3) **google-auth owns token transport, httpx owns `freeBusy`** ("hand-roll the request, never the cryptography"; reject `google-api-python-client`); (4) **availability is a pure clock-injected tz-aware function**, repos sync via `to_thread`.

**Deferred:** multi-operator selection (v1 = one calendar operator/project), multi-calendar selection (v1 = primary calendar), freeBusy result caching, booking/event-creation (read-only first).
