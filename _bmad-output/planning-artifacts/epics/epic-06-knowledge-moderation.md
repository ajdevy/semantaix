# Epic 06: Knowledge Candidate Extraction + Moderation

## Goal
Continuously improve knowledge from conversations without polluting retrieval quality.

## In Scope
- Full transcript retention
- Candidate extraction and noise filtering
- Moderation approve/reject/edit flow
- Reindex on approval
- Incident emission integration into Epic 02 backbone

## Out of Scope
- Backup scheduling/restore controls

## Exit Criteria
- Only approved candidates enter vector index
- Moderation actions are auditable
- Candidate/moderation failures appear in Alerts flow

## Automated E2E verification

- **Transcript extract → approve → RAG retrieve:** **`tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_extract_approve_then_retrievable`** (`@pytest.mark.e2e`).
- Finer-grained API contracts: **`tests/test_api_knowledge_contract.py`**, **`tests/test_api_knowledge_moderation_contract.py`**, repository unit tests under `tests/`.
- Optional scripted signoff: **`scripts/epic06_signoff.sh`** (local operator helper; CI uses pytest).
- Matrix: `_bmad-output/implementation-artifacts/e2e-coverage.md`.
