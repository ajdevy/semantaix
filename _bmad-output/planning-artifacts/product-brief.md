# Semantaix Product Brief

## 1) Vision
Semantaix is a Telegram-first AI support and sales assistant that answers users with RAG-backed responses, escalates uncertain conversations to human operators, and continuously improves knowledge quality through moderated ingestion.

## 2) Problem
Teams answering repetitive customer and pre-sales questions in Telegram lose time, provide inconsistent answers, and struggle to capture operator knowledge for reuse.

## 3) Target Outcome
- Reduce manual response load via reliable AI responses.
- Preserve service quality with human-in-the-loop escalation.
- Build a continuously improving knowledge loop from real conversations.
- Provide strong operational visibility (alerts, logs, health checks).

## 4) Users and Stakeholders
- End users (Telegram customers)
- Human operators (fallback responders)
- Admin/moderator users (knowledge and alert management)
- On-call owner (`@ajdevy`) for incident notifications

## 5) Scope (MVP - Option B)
- Docker-first multi-service architecture.
- FastAPI core service + Telegram gateway + web admin UI.
- Qdrant vector store + PostgreSQL for operational and conversational data.
- RAG retrieval + confidence/fallback policy.
- Human escalation with configurable primary Telegram recipient.
- Alerts tab in web UI (read/unread, ack/resolve, timeline).
- Structured logging, health checks, and critical Telegram incident alerts.
- Full dialog retention in DB + selective knowledge candidate extraction for RAG.

## 6) Out of Scope (Initial MVP)
- Multi-tenant architecture.
- Complex role hierarchy beyond basic admin/operator needs.
- Advanced analytics dashboards beyond operational alerting and logs.

## 7) Success Metrics
- AI deflection rate (questions resolved without human handoff).
- Escalation success rate (handoff delivered and resolved).
- Incident detection and acknowledgement latency.
- Retrieval quality metrics on golden dataset (hit-rate/groundedness).
- Knowledge candidate approval-to-index cycle time.

## 8) Key Non-Functional Requirements
- Reliability: health endpoints and dependency readiness checks.
- Observability: structured logs with trace correlation IDs.
- Resilience: retry/backoff/circuit-breaker for provider failures.
- Operability: Docker-based reproducible deployment on DigitalOcean.
- Safety: only moderated/approved useful knowledge enters vector index.

## 9) Risks
- Noisy conversation data contaminating retrieval quality.
- Alert fatigue from repeated incidents without deduplication/throttling.
- Provider instability causing cascading failures.
- Fragile HITL routing if mapping is not durable and auditable.

## 10) Delivery Strategy
Implement in small, ordered increments (already mapped to Linear issues `FLE-5` through `FLE-18`) starting with infrastructure/bootstrap, then observability/resilience, then core user workflows, then hardening and end-to-end verification.
