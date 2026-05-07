# Story 01.01 — Telegram Webhook Intake and Message Normalization

## Objective
Implement Telegram webhook receiver and normalize incoming payload to internal message format.

## Scope
### In Scope
- `/telegram/webhook` endpoint
- Telegram payload parsing
- Minimal normalized envelope (`external_message_id`, `chat_id`, `user_id`, `text`, `timestamp`, `trace_id`)

### Out of Scope
- LLM calls
- Escalation/HITL
- RAG retrieval
- Incident UI

## Implementation Notes
- Reject malformed payloads with clear 4xx.
- Always generate/store `trace_id`.

## Test Plan
### Unit
- valid payload normalization
- malformed payload rejection
- empty text handling

### Integration
- webhook endpoint receives Telegram-like JSON and returns accepted response

### UI
- N/A for this story

## Automated E2E verification

- **`tests/test_bot_gateway_webhook.py::test_webhook_accepts_text_message_and_returns_trace`** — `@pytest.mark.e2e`; accepts Telegram-like JSON and returns `accepted` plus `trace_id`.
- Narrative coverage also appears in **`tests/test_epic01_e2e.py::test_epic01_e2e_webhook_persist_suggest`** (Story 01.04).

Also see `_bmad-output/implementation-artifacts/e2e-coverage.md`.

## Manual Verification
1. Send sample Telegram update payload to webhook.
2. Confirm API returns accepted status.
3. Confirm normalized structure exists in logs with `trace_id`.

## Done Criteria
- Endpoint implemented
- Unit + integration tests pass
- Manual verification complete
