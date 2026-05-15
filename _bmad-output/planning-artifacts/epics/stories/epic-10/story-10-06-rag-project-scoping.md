# Story 10.06 — RAG retrieval scoping by project_id

## Objective
Make RAG retrieval honor the project boundary: when `/conversations/inbound` resolves to an operator-bound conversation, only chunks belonging to that operator's project (plus pre-migration NULL-project chunks for graceful rollover) are returned. Uploads coming through operator pipelines persist their `project_id` so future retrievals stay scoped.

## Scope

### In Scope
- `services/api/app/rag.py`:
  - Extend `RagChunk` dataclass with `project_id: int | None = None`.
  - `RagRepository.ingest(*, source_id, text, is_confidential=False, project_id=None)` writes `project_id` into each new row.
  - `RagRepository.retrieve(*, query, limit=3, project_id=None)`:
    - When `project_id` is None — current behavior (no filter).
    - When set — `WHERE (project_id = ? OR project_id IS NULL)` so legacy NULL chunks remain reachable but project-scoped chunks of *other* projects are excluded.
- `services/api/app/answerers/__init__.py` — extend `AnswerContext` with `project_id: int | None = None`.
- `services/api/app/answerers/grounded_rag.py` — pass `project_id=ctx.project_id` into `self._rag.retrieve(...)`. Update the `_RagReader` protocol signature.
- `services/api/app/main.py` `/conversations/inbound`:
  - After looking up the HITL ticket for `chat_id`, resolve `project_id`:
    - If an open ticket has an `operator_username`, query `OperatorRepository.find_by_username(...).project_id`.
    - Otherwise fall back to the default project id from `project_repository.ensure_default_project()`.
  - Pass `project_id` into `_build_answer_context(...)`.
- `services/api/app/main.py` upload endpoints:
  - `OperatorUploadRequest` gains `project_id: int | None = None` and `project_slug: str | None = None`. `_perform_operator_upload` resolves `project_slug → project_id` before persisting (precedence: explicit `project_id` > `project_slug` > operator's `project_id` > default).
  - Resolution helper `_resolve_upload_project_id(operator_username, project_id, project_slug) -> int`.
  - Pass `project_id` to `knowledge_moderation_repository.create_approved_operator_upload(...)` and `rag_repository.ingest(...)`.
- `services/api/app/knowledge_moderation.py` — `create_approved_operator_upload(..., project_id: int | None = None)` persists into the new column.
- Update the `web_ui` upload form (forward path) to accept `project_slug` from the form and forward it to api.

### Out of Scope
- Multi-operator routing for inbound (10.07).
- Admin pages / commands (covered in 10.03/04/05).
- Cross-project search.
- Backfilling project_id for historical chunks (NULL-fallback handles it).

## Implementation Notes
- SQL filter:
  ```sql
  SELECT id, source_id, chunk_hash, chunk_text, is_confidential, project_id
  FROM rag_chunks
  WHERE (?1 IS NULL OR project_id = ?1 OR project_id IS NULL)
  ```
  Using `?1` bound twice avoids two query forms.
- Retrieval still scores via lemma overlap (unchanged); the filter only narrows the candidate set.
- `_RagReader` protocol in `grounded_rag.py` was previously `def retrieve(*, query, limit) -> list[RagChunk]`. Extend with `project_id: int | None = None` so test fakes need updating.

## Test Plan

### Unit
- `tests/test_rag_repository_project_scope.py` — ingest with project A and project B, retrieve with `project_id=A` returns only A + NULL chunks, retrieve with `project_id=None` returns all.
- `tests/test_grounded_rag_passes_project_id.py` — fake reader records the `project_id` kwarg.

### API contract
- `tests/test_api_conversations_inbound_project_scope.py` — customer chat with an open ticket assigned to operator-in-project-A returns answer grounded only in A's chunks; customer with no ticket grounds in default project + NULL.

### Integration
- Extend `tests/test_api_operator_upload_*` to cover `project_slug` forwarding.

## Automated E2E verification
- `tests/e2e/test_e2e_epic10_rag_scope.py` — seed projects A & B with distinct chunks, route a customer message via operator-A's ticket, verify answer cites A only.

## Manual Verification
1. Create projects "A" and "B".
2. Assign operator "@op-a" to A, "@op-b" to B.
3. Upload distinct knowledge text into each project (via `/admin/files` or operator `/kb_add` after operator's `project_id` is set).
4. From a customer chat already escalated to "@op-a", ask a question — the answer should cite only A's chunks.

## Done Criteria
- All unit + contract + e2e tests pass.
- 100% coverage on the modified rag and answerer modules.
- `ruff check .` passes.
- Backwards compat: questions answered before the column existed (NULL `project_id`) keep grounding correctly under both scoped and unscoped retrieve.
