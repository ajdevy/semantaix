# Story 05.03 Signoff Checklist

Story: [05.03 — Content-scored retrieval + tokenization hardening + diagnostic logging](../planning-artifacts/epics/stories/epic-05/story-05-03-content-scored-retrieval.md)

## Automated verification

Use **Python 3.11** and a local venv (same as CI); `python3` on macOS Homebrew may be 3.14+ without project deps:

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

Run CI parity:

- [ ] `ruff check .`
- [ ] `pytest --cov --cov-config=.coveragerc --cov-report=term-missing` (must stay at **100% coverage** total).

Targeted (faster while iterating):

- [ ] `pytest tests/test_russian_text_stopwords.py tests/test_rag_repository.py tests/test_answerers_grounded_rag.py -v`

Specific regression nodes:

- [ ] `tests/test_rag_repository.py::test_retrieve_buggy_tour_natural_language_query`
- [ ] `tests/test_rag_repository.py::test_retrieve_score_uses_content_tokens_denominator`
- [ ] `tests/test_rag_repository.py::test_retrieve_stopword_only_query_falls_back`
- [ ] `tests/test_answerers_grounded_rag.py::test_weak_retrieval_falls_through` (caplog reason=`below_threshold`)
- [ ] `tests/test_answerers_grounded_rag.py::test_empty_retrieval_falls_through` (caplog reason=`no_chunks`)
- [ ] `tests/test_answerers_grounded_rag.py::test_sentinel_response_escalates` (caplog reason=`escalate_sentinel`)
- [ ] `tests/test_answerers_grounded_rag.py::test_verifier_not_grounded_escalates` (caplog reason=`verifier_not_grounded`)
- [ ] `tests/test_answerers_grounded_rag.py::test_guardrail_hedge_escalates_even_when_verifier_grounded` (caplog reason=`guardrail_invalid`)
- [ ] `tests/test_answerers_grounded_rag.py::test_profane_llm_output_escalates` (caplog reason=`profanity_detected`)
- [ ] `tests/test_answerers_grounded_rag.py::test_llm_generator_exception_falls_through` (caplog reason=`llm_generator_error`)
- [ ] `tests/test_answerers_grounded_rag.py::test_llm_verifier_exception_falls_through` (caplog reason=`verifier_error`)

## Manual Docker verification

```bash
cp .env.example .env
docker compose up --build -d api
```

API is internal-only (port 8000 is not exposed); use `docker compose exec`:

```bash
# 1. Seed a buggy-tour catalog chunk
docker compose exec -T api python -c '
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8000/rag/ingest",
    data=json.dumps({
        "source_id": "buggy_tour_test",
        "text": "Багги-тур по дюнам. Ежедневно в 9:00. Стоимость 2500 руб. с человека.",
    }).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST",
)
print(json.loads(urllib.request.urlopen(req).read()))
'

# 2. Retrieval regression — must return score 1.0
docker compose exec -T api python -c '
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8000/rag/retrieve",
    data=json.dumps({"query":"хочу поехать на багги тур","limit":3}).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST",
)
print(json.dumps(json.loads(urllib.request.urlopen(req).read()), ensure_ascii=False, indent=2))
'

# 3. Negative control — weather query must NOT match the buggy chunk
docker compose exec -T api python -c '
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8000/rag/retrieve",
    data=json.dumps({"query":"какая погода в антарктиде","limit":3}).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST",
)
print(json.dumps(json.loads(urllib.request.urlopen(req).read()), ensure_ascii=False, indent=2))
'
```

Expected:

- [ ] Step 2 returns one item with `score == 1.0` and `source_id == "buggy_tour_test"`.
- [ ] Step 3 returns `{"items": []}`.

End-to-end (requires a real `OPENROUTER_API_KEY` in `.env`):

- [ ] `POST /conversations/inbound {"text":"хочу поехать на багги тур","chat_id":111,"customer_username":"@test","trace_id":"verify-buggy-1"}` returns `response_mode == "grounded_rag"`, `escalated == false`, no `hitl_ticket_id`.
- [ ] Without a real key, the answerer falls through with `grounded_rag_skipped reason=llm_generator_error` in logs — this is expected and proves the diagnostic logging works.

Diagnostic logging:

- [ ] `docker compose logs api | grep grounded_rag_skipped` shows structured records with `reason=`, `query=`, `top_score=`, `threshold=`, `retrieved_count=`, `chunk_source_ids=` for every escalation.

## Backward-compat checks

- [ ] `pytest tests/test_rag_repository.py::test_retrieve_matches_russian_inflection_via_lemma` passes (lemma matching unchanged).
- [ ] `pytest tests/test_rag_repository.py::test_retrieve_matches_russian_slang_via_normalization` passes (slang substitution still works after the `багги` identity entry is added).
- [ ] `pytest tests/test_rag_repository.py::test_retrieve_scores_and_limits` passes (limit/order semantics unchanged).
- [ ] Existing `tests/test_grounded_rag_*` and `tests/test_api_rag_contract.py` pass without modification.

## Acceptance evidence

- [ ] Test output attached in PR comment.
- [ ] Manual `docker compose exec` output (steps 1–3) captured.
- [ ] Production incident reference: "Artur Yaskevich, 18 May 2026 11:05" — original escalation reproduced before fix, resolved after.
