# Epic 03: Suggestion Quality Guardrails + Valid/Invalid Decision

## Goal
Add deterministic validity checks so only acceptable suggestions are eligible for delivery.

## In Scope
- Validity decision engine (confidence, policy, output-shape, grounding contract)
- Pass/fail reason logging
- Incident emission integration into Epic 02 backbone

## Out of Scope
- HITL operator workflow implementation
- RAG ingestion/retrieval (lemma-overlap; Epic 05)
- Knowledge candidate moderation
- Backup/restore

## Exit Criteria
- Every suggestion has validity decision attached
- Invalid suggestions are blocked from direct delivery path
- Guardrail failures appear in Alerts flow

## Automated E2E verification

- Guardrail block with HITL escalation: **`tests/test_api_hitl_contract.py::test_invalid_suggest_creates_and_assigns_hitl_ticket`** (`@pytest.mark.e2e`; also tagged Epic 04).
- RAG-grounded **passing** suggestion path (retrieval + valid model text): **`tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_then_suggest_includes_retrieval`**.
- Matrix: `_bmad-output/implementation-artifacts/e2e-coverage.md`.
