# Story 05.03 Retrospective — Spurious HITL escalation for natural-language queries

## Incident
On **2026-05-18 11:05 (MSK)** customer "Artur Yaskevich" sent:

> хочу поехать на багги тур

The bot acknowledged with the HITL placeholder (`"Минутку, уточню и вернусь с ответом."`) and DM'd the operator "Анна" instead of answering from RAG, even though the operator's knowledge base contained a buggy-tour catalog file. The operator confirmed: "должен был быть сделан поиск в базе знаний, там есть файлы с багги турами, и необходимо было ответить сразу, без HITL."

## What went wrong
Two distinct bugs combined to drop the retrieval score below the 0.6 grounding threshold:

1. **Scoring formula penalised intent words.** `RagRepository.retrieve` computed `score = overlap / len(query_tokens)`. For "хочу поехать на багги тур" (5 lemmas: `{хотеть, поехать, на, багги, тур}`), only the two content lemmas `{багги, тур}` matched the catalog chunk → score = 2/5 = 0.4 < 0.6. The denominator was diluted by intent / connector lemmas the catalog chunk would never contain.

2. **Razdel kept hyphenated compounds intact.** The catalog chunk read "Багги-тур по дюнам…" — razdel emitted `багги-тур` as a single token, pymorphy3 returned it unchanged, and the query's `{багги, тур}` never matched. With this bug alone, overlap was 0 and retrieval would have returned nothing regardless of the threshold.

There was no diagnostic logging on the `GroundedRagAnswerer` escalation paths, so the operator could not see *which* of the six escalation reasons fired (no_chunks vs below_threshold vs sentinel vs verifier vs guardrail vs profanity vs LLM error) without manually replaying via `POST /rag/retrieve`.

## Why we missed it pre-launch
- Existing RAG tests used multi-word queries dominated by content words (`"password reset"`, `"когда придут деньги"`, `"когда придёт бабло"`) and chunks where intent lemmas happened to overlap (`"когда придут"` ↔ `"когда придёт"`). Real customer phrasing — "хочу X" / "можно ли X" / "как мне X" — was not represented in the test corpus.
- Catalog content was tested with un-hyphenated phrasing ("password reset requires account email"). No fixture exercised hyphenated Russian compounds like `Багги-тур`, `IT-инфраструктура`, `интернет-магазин`.
- `GroundedRagAnswerer` unit tests asserted `handled is False` on each escalation path but did not assert *why* — masking the absence of diagnostic logging.

## What we changed
Story 05.03 ships:
- Content-token denominator in `RagRepository.retrieve` via a new `data/russian_retrieval_stopwords.txt` list of intent / connector / interrogative / preposition / pronoun lemmas. Score is now "fraction of content lemmas of the query that the chunk covers".
- Hyphen pre-split in `rag.py._tokenize` (rag-scoped, not in the shared normalizer where hyphens carry meaning for guardrails).
- `багги` / `buggy` identity entries in `data/russian_slang.json` to pin the loanword's surface form across pymorphy3 versions.
- Structured `grounded_rag_skipped` log on every `GroundedRagAnswerer` escalation branch, carrying `reason`, `query`, `top_score`, `threshold`, `retrieved_count`, `chunk_source_ids`, plus reason-specific extras (`verdict_label`, `guardrail_score`, `error`).
- Regression tests `test_retrieve_buggy_tour_natural_language_query` and `test_retrieve_score_uses_content_tokens_denominator` to prevent re-regression on both the scoring and tokenization paths.

## What we did not change (intentionally)
- `rag_grounding_score_threshold` stays at **0.6**. The threshold's semantic meaning has improved (now "60% of content lemmas covered" rather than "60% of all lemmas including stopwords"), so lowering it on top of the scoring fix would risk false positives.
- No switch to vector retrieval (Qdrant). Qdrant is already provisioned in `docker-compose.yml` but a semantic-search migration is a separate, larger initiative.
- No persistence of `skip_reason` into `answer_traces`. Logging is the operator-facing channel for now; persisting structured skip reasons fits better as an Epic 08 (answer trace UI) follow-up so it can be surfaced in the admin "Why this answer?" view.

## Followups for future stories
- **Epic 08 follow-up**: extend `answer_traces.guardrail_reasons` (currently empty in escalation paths) with the same `skip_reason` set so the admin "Why this answer?" UI explains escalations historically — not just from live logs.
- **Operator workflow**: the existing `GET /admin/files/search` endpoint searches operator file *text*, not RAG chunks. Consider exposing a debug endpoint or admin UI element that runs `RagRepository.retrieve` with explanation (top scores per source, query lemmas after stopword filtering) so operators can self-diagnose "why didn't the bot answer this?" without DM'ing engineering.
- **Test corpus**: add natural-language query fixtures to the RAG test corpus (intent words, hyphenated compounds, single-word loanwords) — every new retrieval feature should be tested against realistic customer phrasing, not just keyword-style queries.
- **Stopword maintenance**: as we observe more escalation logs, additional lemmas may earn a place in `data/russian_retrieval_stopwords.txt`. Treat the file like `russian_hedges.txt` / `russian_policy_phrases.txt` — tunable data, not code.
