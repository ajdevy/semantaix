# Semantaix Architecture (Option B Baseline)

## Stack and Services

- **API** (`services/api`): FastAPI core; conversation, knowledge, trace, and admin HTTP surfaces as they land per epic sequence.
- **Bot gateway** (`services/bot_gateway`): Telegram webhook intake and outbound messaging orchestration.
- **Web UI** (`services/web_ui`): Admin/tenant operator interface (alerts, settings, knowledge, transparency panels per epic).
- **Workers / scheduler** (`services/ingest_worker`, `services/scheduler`): ingestion, reindex, and scheduled jobs as introduced in later epics.
- **PostgreSQL**: system of record for conversations, messages, knowledge entities, audit, incidents, backups metadata.
- **Qdrant**: vector retrieval store; chunk payloads must carry stable ids for trace lineage (**Epic 05**).

## Epic 08 — Data and API Touchpoints (Tenant Knowledge + Answer Traces)

Layered **after** RAG (**Epic 05**), guardrails (**Epic 03**), moderation/reindex (**Epic 06**), and incidents (**Epic 02**).

| Concern | Store / surface | Notes |
|--------|------------------|--------|
| Answer transparency | PostgreSQL **`answer_traces`** (working name): FK to `messages`, `tenant_id`, JSON payload for retrieval hits, guardrail summary, routing, confidence MVP | Append-only; written at decision time; failures → incidents |
| Tenant knowledge | Existing **`knowledge_items` / `knowledge_versions`** (PRD §6); tenant_id column or equivalent isolation | NL ops (**Story 08.03**) create versions; reindex via Epic 05 pipeline |
| Moderation alignment | **`knowledge_candidates`** + Epic 06 workflows | Tunable per tenant: direct publish vs candidate queue |
| Correction linkage | Optional FK from trace to `knowledge_candidates` / version ids | Forward-only lineage for audits |
| API | API routes: trace fetch by message; NL op session endpoints if not bot-only | All routes tenant-scoped |
| UI | Web UI conversation detail → **Why this answer** panel (**Story 08.02**) | Read-only transparency |
| Bot | Trusted tenant admin NL dialogues + confirmations (**Story 08.03**) | Rate limits + allowlists |

No change to Docker-first deployment model; new tables and queues remain Postgres-backed with existing observability conventions (`trace_id`, structured logs).
