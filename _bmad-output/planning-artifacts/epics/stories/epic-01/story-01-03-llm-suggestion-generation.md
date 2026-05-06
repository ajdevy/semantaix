# Story 01.03 — LLM Suggestion Generation from Latest Conversation Context

## Objective
Generate answer suggestions from latest conversation context and return through API/bot path.

## Scope
### In Scope
- Prompt assembly from recent conversation messages
- LLM client wrapper call
- suggestion response formatting and persistence
- Temporary suggestion-only response contract:
  - response includes user-visible suggestion label
  - response includes `is_suggestion_only=true`
  - telemetry includes `response_mode=suggestion_only`, `guardrails_applied=false`

### Out of Scope
- guardrail valid/invalid decision logic (Epic 03)
- HITL escalation routing (Epic 04)
- retrieval from Qdrant (Epic 05)

## Implementation Notes
- Use configurable model/provider settings.
- Log provider latency and outcome with `trace_id`.
- This is pre-guardrail behavior; do not implement validity gating in this story.

## Test Plan
### Unit
- prompt assembly behavior
- provider client success/error handling
- response formatting
- contract assertion for suggestion-only label + flag

### Integration
- webhook -> persisted message -> suggestion generated
- response payload contract includes suggestion-only fields

### UI
- smoke check admin shell availability (existing baseline test)

## Automated E2E verification

- **`tests/test_api_suggest_contract.py::test_suggest_returns_suggestion_payload_on_success`** — `@pytest.mark.e2e`; `/suggest` returns suggestion-only contract with mocked OpenRouter.
- Full stack with webhook persistence: **`tests/test_epic01_e2e.py::test_epic01_e2e_webhook_persist_suggest`**.

Implementation today includes guardrails evaluation on `/suggest` (Epic 03); automated tests cover both passing and blocked model outputs.

See `_bmad-output/implementation-artifacts/e2e-coverage.md`.

## Manual Verification
1. Send Telegram message with test content.
2. Confirm suggestion is generated and returned.
3. Confirm response is persisted and trace-linked in logs.

## Done Criteria
- Suggestion generation operational
- Unit + integration tests pass
- Manual verification complete
- suggestion-only contract is consistently present in all Epic 01 responses
