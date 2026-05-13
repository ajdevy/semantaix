# Story 09.04 — API endpoint, schema migration, auto-approval, confidential redaction

## Objective
Land the `/knowledge/operator_upload` endpoint with auto-approval, propagate `is_confidential` into RAG, and redact confidential chunks in `GroundedRagAnswerer` audit metadata.

## Scope

### In Scope
- Migration on `knowledge_moderation_candidates` (additive, idempotent via `PRAGMA table_info`): add `uploaded_by_operator_username TEXT`, `is_confidential INTEGER NOT NULL DEFAULT 0`, `source_file_name TEXT`, `source_file_type TEXT`, `stored_binary_path TEXT`, `binary_sha256 TEXT` (indexed).
- Migration on `rag_chunks`: add `is_confidential INTEGER NOT NULL DEFAULT 0`. Extend `RagChunk` dataclass with `is_confidential: bool = False`. `RagRepository.ingest` takes `is_confidential: bool = False`; `retrieve` hydrates it.
- New repo methods on `KnowledgeModerationRepository`:
  - `create_approved_operator_upload(*, candidate_text, published_text, operator_username, is_confidential, source_file_name, source_file_type, stored_binary_path, binary_sha256) -> KnowledgeCandidateRow`.
  - `find_by_binary_sha256(sha256) -> KnowledgeCandidateRow | None`.
- New route in `services/api/app/main.py`: `POST /knowledge/operator_upload` with pydantic models `OperatorUploadRequest` and `OperatorUploadResponse`. Handler order:
  1. Validate `source_file_type` against the closed enum; if not `inline_text`, validate `stored_binary_path` exists.
  2. For non-`inline_text`: compute `binary_sha256`; on `find_by_binary_sha256` hit, return `deduplicated=True` immediately (zero extraction).
  3. Dispatch via `EXTRACTORS`; soft-wrap; 422 on empty.
  4. `create_approved_operator_upload` with `published_text=wrapped`, `binary_sha256=sha`.
  5. `rag_repository.ingest(source_id=f"knowledge_candidate:{candidate_id}", text=wrapped, is_confidential=request.is_confidential)`.
  6. Return summary.
  7. Unexpected exceptions → `incident_repository.ingest("operator_upload_failures", "critical", …)` and 500.
- `services/api/app/answerers/grounded_rag.py`: when building `metadata["retrieval"]`, if `chunk.is_confidential`, emit `{"source_id": "knowledge_candidate:confidential", "chunk_text": "[redacted]", "score": chunk.score, "is_confidential": True}`. The actual `chunk_text` still flows into `llm.answer_grounded` and `verify_grounding`. Customer answer text is unchanged.

### Out of Scope
- Bot orchestration (09.05), Docker/infra (09.05).

## Implementation Notes
- No `OpenRouterClient` changes. No vision method.
- `rag_chunks` and `knowledge_moderation_candidates` live in separate SQLite DB files — denormalize `is_confidential` onto the chunk so retrieval avoids cross-DB joins.

## Test Plan

### Unit
- `tests/test_knowledge_moderation_repository_operator.py` (`create_approved_operator_upload` writes all new columns; idempotent migration when the schema already has the columns).
- `tests/test_rag_repository_confidential.py` (round-trip ingest+retrieve preserves the flag).
- `tests/test_grounded_rag_confidential_redaction.py` (fake repository returns a confidential chunk; assert redacted metadata + spy verifies the LLM received the real chunk text).

### Integration
- `tests/test_api_operator_upload.py` (FastAPI TestClient): each `source_file_type` with extractors monkey-patched; asserts `status='approved'`, all new columns set, RAG chunk count, correct `is_confidential` per chunk. Dedup short-circuit: posting twice produces one candidate, second response has `deduplicated=True, inserted_chunks=0`, extractor spy never called. Error paths: empty extraction → 422, missing path → 404, extractor raises → 500 + incident row.

## Automated E2E verification
- `tests/e2e/test_e2e_epic09_operator_upload.py::test_epic09_pdf_upload_then_grounded_answer` (`@pytest.mark.e2e`): hits the api with a fixture PDF, then issues `/conversations/inbound` and asserts the answer grounds on the new chunks.

## Manual Verification
1. `curl -X POST http://localhost/api/knowledge/operator_upload -d '{...}'` against a fixture PDF on disk; verify candidate row and chunks.
2. Repeat to confirm dedup.
3. Re-run with `is_confidential=true` and confirm the answer trace shows redacted metadata.

## Done Criteria
- All tests pass.
- 100% coverage on new/changed modules in `services/api/app/`.
- `ruff check .` passes.
