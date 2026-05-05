# Story 01.04 — Epic 01 E2E Verification and Demo Signoff

## Objective
Provide reproducible proof that Epic 01 works end-to-end and is ready for gate signoff.

## Scope
### In Scope
- End-to-end test path: webhook -> normalize -> persist -> LLM suggestion
- Demo script and checklist
- Regression snapshot for Epic 01 baseline

### Out of Scope
- Incident/alerts workflows (Epic 02)
- Any later epic feature

## Test Plan
### Unit
- N/A (story is integration/signoff focused)

### Integration
- E2E test covering full Epic 01 flow

### UI
- basic admin shell smoke remains green

## Manual Verification
1. Run automated test suite for Epic 01.
2. Execute scripted Telegram flow in staging/local.
3. Capture output evidence and confirm acceptance checklist.

## Done Criteria
- E2E test green
- Demo evidence recorded
- Epic 01 signoff checklist completed
