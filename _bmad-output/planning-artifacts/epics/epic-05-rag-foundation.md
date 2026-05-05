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
