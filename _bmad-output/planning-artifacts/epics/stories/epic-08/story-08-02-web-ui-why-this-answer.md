# Story 08.02 — Web UI: “Why This Answer” Transparency Panel

## Objective
Expose answer traces in the Web UI so tenant staff can inspect **why** an end-customer saw a specific response (sources, policy, routing, limitations).

## Scope
### In Scope
- Conversation/message detail view extension: **Answer trace** panel (collapsible sections):
  - **Sources**: retrieved chunks with scores and truncated text; link to source document id when available.
  - **Policy / guardrails**: outcome, failed checks, escalation vs send decision.
  - **Model routing**: model name, provider, key latency stats.
  - **Confidence / limitations**: grounded flag, confidence, explicit caveats.
- Read-only access for tenant role; respect tenant scope on API queries.
- Empty/error states: trace missing, partial trace, incident reference.

### Out of Scope
- Inline editing of knowledge from this panel (Story 08.04 entry point only as a button stub is acceptable)
- End-user Telegram UI for transparency

## Implementation Notes
- Reuse existing Web UI layout and auth patterns from prior epics.
- API: `GET` trace by `message_id` (or by `trace_id`) with tenant guard.
- Performance: avoid loading full chunk text; use capped snippets.

## Test Plan
### Unit
- UI state mapping from API DTO to sections

### Integration
- API returns trace; UI renders all sections for golden fixture

### UI
- Playwright or existing UI smoke pattern: open conversation → panel visible

## Automated E2E verification

Minimal **HTTP smoke only** until trace APIs and panels exist:

- **`tests/e2e/test_e2e_epic08_web_ui_smoke.py::test_epic08_admin_shell_reachable`** (`@pytest.mark.e2e`) — static admin landing page responds 200.

Full transparency panel coverage remains **TODO** alongside Story 08.01 API work.

## Manual Verification
1. Seed a message with trace fixture.
2. Open Web UI conversation; verify all sections match fixture.
3. Confirm unauthorized tenant cannot access trace.

## Done Criteria
- Panel shipped behind feature flag if required
- Tests pass
- Documentation: one screenshot or short Loom note in sprint closure (optional, team norm)
