# Story 08.01 — Answer Trace Schema and Persistence (MVP)

## Objective
Define and persist immutable **answer trace** records for each suggestion or delivered answer attempt, capturing enough structure for transparency and downstream correction—without reinventing Epic 03/05 internals.

## Scope
### In Scope
- PostgreSQL entities for **`answer_traces`** (name may match final migration) keyed by `message_id` / `trace_id` lineage:
  - **Retrieval**: list of `{chunk_id, source_ref, score, text_snippet_truncated}` (actual shape aligned to Qdrant payload from Epic 05).
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

## Manual Verification
1. Trigger a test answer through dev stack.
2. Confirm row exists with expected lineage keys and retrieval/guardrail sections populated from fixtures.

## Done Criteria
- Migration + repository + minimal writer API merged
- Tests pass
- Epic 02 hook verified (or mocked contract test)
- Alerts on hard failures documented in runbook note (one paragraph in story closure)
