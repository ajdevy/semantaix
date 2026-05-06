# Epic 05: RAG Foundation (Ingestion + Retrieval)

## Goal
Introduce retrieval-backed suggestion context with source lineage.

## In Scope
- Source ingestion/chunking/vectorization pipeline
- Retrieval service integration into suggestion generation
- Minimal retrieval quality metrics
- Incident emission integration into Epic 02 backbone

## Out of Scope
- Knowledge candidate moderation workflow
- Backup/restore controls

## Exit Criteria
- Suggestions can be grounded with retrieved context
- Ingestion and retrieval validated on sample corpus
- RAG failures appear in Alerts flow

## Automated E2E verification

- **Ingest → `/suggest` with non-empty `retrieval`:** **`tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_then_suggest_includes_retrieval`** (`@pytest.mark.e2e`).
- Contract-level ingest/retrieve remains in **`tests/test_api_rag_contract.py`** (not all marked `@e2e`).
- Matrix: `_bmad-output/implementation-artifacts/e2e-coverage.md`.
