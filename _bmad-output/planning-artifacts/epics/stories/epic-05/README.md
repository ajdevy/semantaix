# Epic 05 Story Pack

Epic: RAG Foundation (Ingestion + Retrieval)

Story 05-01 (foundation: ingest/chunk/retrieve) and 05-02 (grounding integration with `/suggest`) were delivered as part of the initial epic shipping and are tracked in the e2e coverage matrix without dedicated story files. Stories added here are post-shipping refinements that change scoring or retrieval semantics.

| Story | Status | Trigger |
|-------|--------|---------|
| 05-03 | shipped | Production incident: natural-language query "хочу поехать на багги тур" escalated to HITL despite the knowledge base containing a matching buggy-tour catalog chunk. |

## Automated E2E (current repo)

Post-shipping fixes land in the existing `tests/test_rag_repository.py` and `tests/test_answerers_grounded_rag.py` (unit + contract). Story-level rows live in [`../../implementation-artifacts/e2e-coverage.md`](../../../../implementation-artifacts/e2e-coverage.md).
