# Epic 04: Human-in-the-Loop Escalation (Bot-Authored Delivery)

## Goal
Route invalid/uncertain cases to human operators and send operator responses back as bot-authored messages.

## In Scope
- Escalation ticket lifecycle
- Configurable primary operator username
- Admin-gated runtime HITL configuration via Telegram command (`/hitl_config`)
- Operator reply-to-ticket mapping
- Bot-authored outbound response (no operator metadata leakage)
- Incident emission integration into Epic 02 backbone

## Out of Scope
- Qdrant ingestion/retrieval
- Knowledge candidate extraction/moderation
- Backup/restore

## Exit Criteria
- Escalation round-trip works end-to-end
- User never sees operator identity metadata
- HITL delivery failures appear in Alerts flow
- Only configured admin username can update runtime HITL recipient/chat routing
