# Epic 03: Suggestion Quality Guardrails + Valid/Invalid Decision

## Goal
Add deterministic validity checks so only acceptable suggestions are eligible for delivery.

## In Scope
- Validity decision engine (confidence, policy, output-shape, grounding contract)
- Pass/fail reason logging
- Incident emission integration into Epic 02 backbone

## Out of Scope
- HITL operator workflow implementation
- Qdrant ingestion/retrieval
- Knowledge candidate moderation
- Backup/restore

## Exit Criteria
- Every suggestion has validity decision attached
- Invalid suggestions are blocked from direct delivery path
- Guardrail failures appear in Alerts flow
