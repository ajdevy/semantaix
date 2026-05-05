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
