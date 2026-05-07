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

## Automated E2E verification

- Journey (**blocked suggest → route → resolve**): **`tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_guardrail_blocked_suggest_then_route_and_resolve`** (`@pytest.mark.e2e`).
- Ticket creation from guardrails: **`tests/test_api_hitl_contract.py::test_invalid_suggest_creates_and_assigns_hitl_ticket`**.
- Operator reply (mock Telegram): **`tests/test_api_hitl_contract.py::test_hitl_reply_delivered_as_bot_authored`**.
- Runtime `/hitl_config`: **`tests/test_bot_gateway_webhook.py::test_admin_can_configure_hitl_contact_via_command`**.
- Matrix: `_bmad-output/implementation-artifacts/e2e-coverage.md`.
