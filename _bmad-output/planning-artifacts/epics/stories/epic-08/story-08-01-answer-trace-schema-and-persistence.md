# Story 08.01 — Answer Trace Schema and Persistence (MVP)

## Objective
Define and persist immutable **answer trace** records for each suggestion or delivered answer attempt, capturing enough structure for transparency and downstream correction—without reinventing Epic 03/05 internals.

## Scope
### In Scope
- SQLite table **`answer_traces`** (`semantaix_answer_traces.db`) keyed by a unique `trace_id`, with optional `hitl_ticket_id` linkage:
  - **Retrieval**: `retrieval_json` — list of `{chunk_id, source_ref, score, text_snippet}` (snippet truncated to ~240 chars; shape aligned to the Epic 05 lemma-overlap retrieval payload).
  - **Routing**: model id, provider, temperature/top_p if applicable, latency ms.
  - **Guardrails**: validity outcome, failed check ids/reasons, `response_mode`, `guardrails_applied`.
  - **Grounding/confidence MVP**: grounded flag, numeric confidence if available, “no retrieval hit” indicator.
  - **Limitations**: bounded text field or enum list (e.g. `partial_context`, `policy_blocked`).
- API internal hooks for suggestion/delivery pipeline to write trace row **after** guardrail decision (Story defers plumbing to Epic 03/05 integration points; this story owns schema + repository + failing alerts).
- Idempotency: duplicate webhook retries must not duplicate traces (unique constraint strategy).

### Out of Scope
- Full Web UI (Story 08.02)
- NL knowledge operations (Story 08.03)
- Moderation UX (Story 08.04; optional flag only stubbed here if needed for FK placeholders)

## Implementation Notes
- Treat traces as **append-only**; corrections create new knowledge versions, never rewrite history.
- Trace payload size caps and PII redaction rules must match logging policy in PRD observability sections.
- On persistence failure, emit **Epic 02** incident with severity consistent with observability ladder.

## Test Plan
### Unit
- serialization/validation of trace payload
- idempotency on duplicate write keys

### Integration
- simulated answer path persists trace row with Epic 03/05 stub/fixture payloads
- failure path emits incident stub (or mocked notifier)

### UI
- not required for this story

## Automated E2E verification

Implemented (`services/api/app/answer_trace.py`). Covered by
`tests/e2e/test_e2e_epic08_answer_trace.py::test_epic08_suggest_writes_queryable_trace`
plus `tests/test_answer_trace_repository.py` and `tests/test_api_answer_trace_contract.py`.
See `_bmad-output/implementation-artifacts/e2e-coverage.md`.

## Manual Verification
1. Trigger a test answer through dev stack.
2. Confirm row exists with expected lineage keys and retrieval/guardrail sections populated from fixtures.

## Done Criteria
- Migration + repository + minimal writer API merged
- Tests pass
- Epic 02 hook verified (or mocked contract test)
- Alerts on hard failures documented in runbook note (one paragraph in story closure)
