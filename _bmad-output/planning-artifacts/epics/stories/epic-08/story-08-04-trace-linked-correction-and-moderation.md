# Story 08.04 — Trace-Linked Correction, Moderation Alignment, and Audit

## Objective
Close the loop: from a **specific answer trace**, let the tenant **correct** underlying knowledge so **future** answers change, with optional **Epic 06** moderation gate and a clear audit chain.

## Scope
### In Scope
- Web UI and/or bot action: **“Fix knowledge from this answer”** pre-fills a draft using trace sources (top chunks + user question context); user edits in guided form or NL (re-enter Story 08.03 pipeline).
- Branching:
  - **Direct publish** path when tenant policy allows (reindex immediately).
  - **Moderation** path: submit as `knowledge_candidate` per **Epic 06**; trace stores `correction_candidate_id` link.
- **audit_logs**: who opened trace, who submitted correction, moderation decisions, reindex completion.
- Notification (optional): tenant sees “your correction is live” or “pending review.”

### Out of Scope
- Automatic correction from end-user feedback without human confirm
- Rewriting historical Telegram messages

## Implementation Notes
- Never mutate prior `answer_traces`; link forward-only.
- Ensure tenant isolation on all candidate and knowledge records.
- If reindex fails, incident + user-visible retry state.

## Test Plan
### Unit
- mapping trace → draft payload
- policy branch selection (direct vs moderation)

### Integration
- trace → candidate → approve (Epic 06 fixture) → retrieval reflects change
- audit log sequence assertions

### UI
- click-through from Story 08.02 panel to correction flow

## Manual Verification
1. Open trace for a wrong answer; submit correction.
2. With moderation on, verify candidate appears in Epic 06 queue.
3. Approve; verify new answer uses updated knowledge in manual RAG check.

## Done Criteria
- Loop complete with tests
- Documentation cross-links Epic 06 moderation states
- Failure modes covered with incidents
