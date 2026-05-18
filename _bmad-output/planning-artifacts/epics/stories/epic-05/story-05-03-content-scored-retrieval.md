# Story 05.03 — Content-scored retrieval + tokenization hardening + diagnostic logging

## Objective
Stop `GroundedRagAnswerer` from spuriously escalating natural-language customer questions to HITL when the knowledge base actually contains a matching catalog chunk. Two production issues drive this story:

1. **Scoring**: `RagRepository.retrieve` divides chunk overlap by the full set of query lemmas. Intent / connector lemmas (`хотеть`, `поехать`, `на`) deflate the denominator so a query like "хочу поехать на багги тур" (5 lemmas) against a chunk that contains only `{багги, тур}` lands at score = 2/5 = 0.4, below the default `grounding_threshold` of 0.6 — escalated.
2. **Tokenization**: razdel keeps hyphenated compounds like `Багги-тур` as a single token, so the chunk's `{багги-тур}` never matches the query's `{багги, тур}` — overlap = 0 even when the words obviously match.

Also: `GroundedRagAnswerer` has no diagnostic logging on its `handled=False` branches, so operators cannot determine post-hoc why a message escalated (no_chunks vs below_threshold vs sentinel vs verifier vs guardrail vs profanity vs LLM error).

## Scope

### In Scope
- `services/api/app/rag.py`:
  - `_tokenize(text)`: pre-replace `-` with space before delegating to `RussianNormalizer.lemmas`, so `Багги-тур` becomes `[багги, тур]` on both query and chunk sides.
  - `retrieve(...)`: subtract `get_retrieval_stopwords()` from the query lemmas to form `content_tokens`. Use `content_tokens` (or full `query_tokens` as fallback when content is empty) as both the overlap numerator AND the denominator so score stays in `[0, 1]`. Chunk tokens are never filtered.
- `services/api/app/russian_text/stopwords.py` (new): `load_retrieval_stopwords(path) -> frozenset[str]` and cached `get_retrieval_stopwords()` singleton. Mirrors the `profanity.py` loader pattern.
- `services/api/app/russian_text/__init__.py`: re-export both helpers.
- `data/russian_retrieval_stopwords.txt` (new): newline-delimited intent / desire / motion / interrogative / connector / preposition / pronoun lemmas. `#` comments and blank lines tolerated.
- `data/russian_slang.json`: add `"багги": "багги"` and `"buggy": "багги"` identity entries so pymorphy3's hypothetical declension of the loanword cannot drift between query and chunk sides.
- `services/api/app/answerers/grounded_rag.py`:
  - Module-level `logger = logging.getLogger(__name__)`.
  - Helper `_skip(reason, *, ctx, question, chunks, **extra) -> AnswerResult(handled=False)` logs `grounded_rag_skipped` with `trace_id`, `reason`, `query`, `threshold`, `retrieved_count`, `top_score`, `chunk_source_ids` plus any extras.
  - Every `return AnswerResult(handled=False)` replaced with `return self._skip(...)`. Reasons: `no_chunks`, `below_threshold`, `llm_generator_error`, `escalate_sentinel`, `verifier_error`, `verifier_not_grounded`, `guardrail_invalid`, `profanity_detected`.

### Out of Scope
- Switching to vector retrieval (Qdrant) — already provisioned in `docker-compose.yml` but a separate initiative.
- Changing the default `rag_grounding_score_threshold` (stays at 0.6).
- Public API changes to `/conversations/inbound`, `/rag/retrieve`, `/rag/ingest`.
- Persisting `skip_reason` into `answer_traces` (consider in epic-08 follow-up).
- Backfilling historical chunks; the hyphen-split fix re-tokenizes on read, so existing rows are covered without ingest changes.

## Implementation Notes
- Score formula:
  ```python
  content_tokens = query_tokens - get_retrieval_stopwords()
  scoring_tokens = content_tokens or query_tokens
  overlap = len(scoring_tokens & chunk_tokens)
  score = overlap / max(len(scoring_tokens), 1)
  ```
  Using `content_tokens` on both sides keeps score in `[0, 1]`. The fallback to `query_tokens` for stopword-only queries (e.g. "Что? Как?") prevents an accidental score of 1.0 against arbitrary chunks.
- Hyphen pre-split is rag-scoped (lives in `rag.py._tokenize`, not the shared `RussianNormalizer`) because other consumers (guardrails, profanity check) operate on full text where hyphens carry meaning.
- Stopword list is intentionally Russian-only and tuned for retrieval scoring. **Do NOT reuse** for guardrails, profanity, or LLM-output filtering — the list contains words ("я", "мы") that are perfectly valid in those contexts.

## Test Plan

### Unit
- `tests/test_russian_text_stopwords.py` (new) — loader strips comments / blank lines, lowercases entries, handles default path, returns cached singleton, resolves to `data/russian_retrieval_stopwords.txt`.
- `tests/test_rag_repository.py`:
  - `test_retrieve_buggy_tour_natural_language_query` — regression: "хочу поехать на багги тур" against "Багги-тур по дюнам. Ежедневно в 9:00. Стоимость 2500 руб." returns the chunk with `score >= 0.6` and `<= 1.0`.
  - `test_retrieve_score_uses_content_tokens_denominator` — query "багги тур" against a chunk with both lemmas scores 1.0.
  - `test_retrieve_stopword_only_query_falls_back` — query "что как где" returns no false-positive score 1.0.
  - Existing `test_retrieve_scores_and_limits`, `test_retrieve_matches_russian_inflection_via_lemma`, `test_retrieve_matches_russian_slang_via_normalization` keep passing unchanged.
- `tests/test_answerers_grounded_rag.py`:
  - `caplog`-based assertions on every escalation path asserting `grounded_rag_skipped` log with the expected `reason`, `query`, `threshold`, `retrieved_count`, `top_score`, and `chunk_source_ids` extras.

### Manual Verification (Docker)
1. `cp .env.example .env && docker compose up --build -d api`
2. Seed: `POST /rag/ingest {"source_id":"buggy_tour_test","text":"Багги-тур по дюнам. Ежедневно в 9:00. Стоимость 2500 руб. с человека."}`.
3. Verify retrieval: `POST /rag/retrieve {"query":"хочу поехать на багги тур","limit":3}` → top item has `score == 1.0`.
4. Verify pipeline (with a real `OPENROUTER_API_KEY` in `.env`): `POST /conversations/inbound {"text":"хочу поехать на багги тур","chat_id":111,"customer_username":"@test","trace_id":"verify-buggy-1"}` → `response_mode == "grounded_rag"`, `escalated == false`, no `hitl_ticket_id`.
5. Negative control: `POST /rag/retrieve {"query":"какая погода в антарктиде"}` → `[]` (no false match against the buggy chunk).
6. Diagnostic logs: `docker compose logs api | grep grounded_rag_skipped` for any escalation — structured `reason=` is present.

## Done Criteria
- All unit + contract tests pass.
- 100% coverage maintained on `services/api/app/rag.py`, `services/api/app/answerers/grounded_rag.py`, `services/api/app/russian_text/stopwords.py`.
- `ruff check .` passes.
- Manual Docker verification passes for the regression query and the negative control.
- Existing retrieval tests (slang, inflection, scoring/limits) keep passing — no regression on lemma matching.
- Pre-shipping behaviour: deployments without the new stopwords file or with an unwritable `data/` directory fail loudly (the loader raises) — this is by design; the file is shipped in-repo, not optional configuration.

## Files Touched

| Path | Change |
|------|--------|
| `data/russian_retrieval_stopwords.txt` | new — stop lemma list |
| `data/russian_slang.json` | added `багги`/`buggy` identity entries |
| `services/api/app/russian_text/stopwords.py` | new — loader + cached singleton |
| `services/api/app/russian_text/__init__.py` | re-export `load_retrieval_stopwords`, `get_retrieval_stopwords` |
| `services/api/app/rag.py` | hyphen pre-split in `_tokenize`; content-token scoring in `retrieve` |
| `services/api/app/answerers/grounded_rag.py` | diagnostic logger + `_skip` helper |
| `tests/test_russian_text_stopwords.py` | new — loader tests |
| `tests/test_rag_repository.py` | regression + content-token tests |
| `tests/test_answerers_grounded_rag.py` | caplog assertions on every escalation path |
