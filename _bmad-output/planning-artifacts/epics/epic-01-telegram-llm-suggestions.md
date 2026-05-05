# Epic 01: Telegram Conversation Intake + LLM Answer Suggestions

## Goal
Read Telegram user messages and generate LLM answer suggestions with only minimal persistence and observability required for this feature.

## In Scope
- Telegram webhook ingestion and normalization
- Conversation/message persistence (minimum required shape)
- Prompt assembly from latest conversation context
- Suggested-answer generation endpoint/flow
- Basic health endpoints and trace logging needed for this epic
- Temporary suggestion-only response contract:
  - user-visible suggestion label
  - `is_suggestion_only=true`
  - telemetry: `response_mode=suggestion_only`, `guardrails_applied=false`
- Standardized Telegram fixture matrix for Story 01 validation

## Out of Scope
- Incident management UI/notifier workflows
- Guardrail validity engine
- HITL escalation
- Qdrant retrieval/indexing
- Knowledge extraction/moderation
- Backup/restore

## Exit Criteria
- Telegram user message is received and persisted
- LLM suggestion is generated and returned reliably
- Automated tests pass for webhook -> persist -> suggest
- Manual demo script passes end-to-end
- Minimal persistence boundary is enforced as documented (no over-modeling)
- Fixture matrix outcomes are implemented and passing
