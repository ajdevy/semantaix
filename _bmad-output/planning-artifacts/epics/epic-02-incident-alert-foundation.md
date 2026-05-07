# Epic 02: Incident Management + Alerts UI + Telegram Critical Notify

## Goal
Establish the operational safety net early so all later epics attach their failures to one incident/alert backbone.

## In Scope
- Incident model and dedup lifecycle
- Alerts tab with read/unread + ack/resolve + timeline
- Critical Telegram notification path to `@ajdevy`
- Baseline incident taxonomy for Epic 01 failure modes

## Out of Scope
- Guardrail-specific validity logic
- HITL ticket workflow
- RAG ingestion/retrieval and moderation pipelines
- Backup/restore workflows

## Exit Criteria
- Critical incident classes alert correctly
- Alerts UI lifecycle works and persists
- Epic 01 failure modes are represented in incident pipeline

## Automated E2E verification

- Alerts API lifecycle (create → timeline → read → acknowledge → resolve): **`tests/test_api_incidents_contract.py::test_incident_read_ack_resolve_and_timeline`** (`@pytest.mark.e2e`).
- Full matrix: `_bmad-output/implementation-artifacts/e2e-coverage.md`.
