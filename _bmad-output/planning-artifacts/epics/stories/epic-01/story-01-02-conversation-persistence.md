# Story 01.02 — Conversation and Message Persistence (Minimal)

## Objective
Persist incoming normalized Telegram messages into conversation/message model required for suggestion generation.

## Scope
### In Scope
- Minimal DB schema additions for epic flow
- create-or-get conversation behavior
- append message records with role/source metadata
- Explicit minimal schema boundary:
  - `conversations`: `id`, `telegram_user_id`, `created_at`, `updated_at`
  - `messages`: `id`, `conversation_id`, `source_message_id`, `role`, `text`, `trace_id`, `created_at`
- Idempotency constraint on `source_message_id`

### Out of Scope
- Full escalation schema
- incident lifecycle schema
- candidate extraction schema

## Implementation Notes
- Keep schema minimal for Epic 01.
- Ensure idempotency for repeated webhook delivery of same Telegram message ID.
- Do not add escalation/incident/moderation/backup tables in this story.

## Test Plan
### Unit
- repository create/get behavior
- idempotent write behavior
- trace metadata persistence

### Integration
- webhook intake writes expected conversation/message rows
- duplicate update fixture does not create duplicate `messages` row

### UI
- N/A for this story

## Manual Verification
1. Send two messages from same user.
2. Verify one conversation and two messages persisted.
3. Re-send same webhook event and verify no duplicate message row.

## Done Criteria
- Minimal persistence complete
- Unit + integration tests pass
- Manual verification complete
- Schema matches locked boundary exactly (no extra lifecycle tables)
