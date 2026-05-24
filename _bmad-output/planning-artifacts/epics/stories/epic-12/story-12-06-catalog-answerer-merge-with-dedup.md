# Story 12.06 — Catalog answerer merge-with-dedup + `services_render` + `grounding_system` rule

## Objective
Cut `GroundedRagAnswerer`'s catalog-query branch over to the canonical `project_services` table while preserving brownfield continuity. The branch reads structured rows first, calls the existing `_catalog_digest.get_digest(...)` for the same project, **merges with lemma-based deduplication** (`RussianNormalizer.lemmas`; structured row wins on conflict; over-include is safer than under-include), and renders each structured row to **natural Russian prose at the repository boundary with NO field labels** via the new `services_render.render_project_service_prose(...)` helper (which reads `data/russian_calendar_terms.json` from story 12.01). Writes `answer_traces.source_id` = `project_services:<id>` / `catalog_digest:<id>` / `merged:<id>` depending on which sources contributed. Extends the `grounding_system` prompt (via `project_prompt_repository`) with the FR-25 humanistic + question-tailored Russian rule. **This story depends ONLY on 12.01** (it consumes the repository + data file; it does NOT depend on the NL branch 12.02→12.05). Architecture reference: lines 232, 237, 250, 263; FR reference: FR-25.

## Scope

### In Scope
- **New module `services/api/app/services_render.py`** with `render_project_service_prose(service, *, terms_data) -> str`:
  - Returns natural Russian prose per row: `"Маникюр — 60 минут, пн–сб 10:00–19:00, цена от 2000 ₽. Классический и аппаратный."`.
  - **NO field labels** (`Название:` / `Описание:` / `Цена:` / `Длительность:` / `Дни:` / `Часы:` never appear in output).
  - Skips empty fields cleanly (a row with only `name` renders as `"Маникюр."`; a row with `name` + `price_text` renders as `"Маникюр — цена от 2000 ₽."`).
  - **Working hours rendering:** single window per day → `"пн 10:00–19:00"`; multi-window per day → `"пн 10:00–13:00, 14:00–19:00"`; day grouping consecutive identical-hours days as ranges (`"пн–сб 10:00–19:00"` when mon..sat all share the same window) is a stretch goal — the v1 acceptable form is per-day enumeration if grouping adds risk.
  - **Service days rendering** (when no working_hours): `"пн, ср, пт"` (short codes from terms data).
  - **Date exceptions rendering:** `"закрыто: 1 января, 9 мая"` (genitive month from terms data; `closed_prefix` from terms data).
  - **Terms data loaded once** at module import via lazy `@functools.cache` from `data/russian_calendar_terms.json` (path resolved via `settings`); reader is pure (no I/O at render time after first call).
- **New module `services/api/app/services_catalog_merge.py`** with `merge_structured_with_digest(structured_rows, digest_text, *, normalizer) -> tuple[str, str]`:
  - Returns `(merged_chunk_text, source_id_suffix)` where `source_id_suffix ∈ {"project_services","catalog_digest","merged"}` per the four FR-25 branches.
  - **Dedup algorithm:** for each `structured_row`, compute `name_lemmas = set(normalizer.lemmas(row.name))`. Tokenize `digest_text` into sentences (or paragraphs — pick one and pin); for each digest sentence, compute its lemma set; if `name_lemmas ⊆ sentence_lemmas` (the structured name's lemmas all appear in that sentence's lemmas), mark the sentence as "covered by structured row" and DROP it from the digest contribution. Structured prose ALWAYS wins.
  - **When in doubt, both kept** — if `name_lemmas` is empty (degenerate name like a single ё-only token) OR the digest tokenization yields no sentences, fall back to "no dedup" (concatenate structured prose + full digest). Over-include is safer than under-include per FR-25.
  - **Four-branch source_id:** structured empty + digest empty → `_skip(reason='catalog_empty')` (caller handles via existing fall-through); structured empty + digest non-empty → digest only, suffix `catalog_digest`; structured non-empty + digest empty → structured only, suffix `project_services`; both non-empty → merged with dedup, suffix `merged`.
- **Extended `services/api/app/answerers/grounded_rag.py`** catalog branch:
  - Reads `to_thread(ProjectServiceRepository.list_for_project, ctx.project_id)` (NEW — currently reads only `_catalog_digest.get_digest`).
  - Calls `_catalog_digest.get_digest(project_id=ctx.project_id)` (UNCHANGED — pre-existing path).
  - Calls `merge_structured_with_digest(structured_rows, digest_text, normalizer=self._normalizer)`.
  - Wraps the merged text as a single `RagChunk` and passes it to the existing `answer_grounded` LLM step (NO additional LLM call relative to today's digest path).
  - Writes `answer_traces.source_id = f"{source_id_suffix}:{ctx.project_id}"` via the existing trace plumbing.
- **`grounding_system` prompt extension** — add the FR-25 Russian humanistic + question-tailored rule via `services/api/app/project_prompts.py`'s `project_prompt_repository`:
  - New rule text: `"Если клиент просто спрашивает, какие есть услуги — перечисли только названия, естественно и кратко. Если клиент спрашивает про цену, детали, описание или конкретную услугу — добавь только то, что он спросил. Не дампи всё подряд."`
  - Attached to the `grounding_system` slot (next to existing rules); applied to every project (the repository's "global default" path) so brownfield projects pick it up without per-project config.
  - A migration helper writes the new rule once; idempotent on subsequent boots (existence check before insert).
- **Guardrails audit (release-readiness)** — read `data/russian_hedges.txt` and assert none of the typical price/duration phrasings ("от 2000 ₽", "от 60 минут", "от 2000", "от 60") appear as hedge entries. If any do, remove them and document in the decision log; the audit lives as an assertion in `tests/test_russian_hedges_audit.py`.

### Out of Scope
- LLM-based extraction of services from `/kb_add` PDFs into `project_services` (future epic per FR-23 deferral).
- Adding new hedge categories to `data/russian_hedges.txt` — the audit only *removes* false-positive entries that would block legitimate catalog answers.
- The NL dialog branch (12.04 / 12.05) — this story is independent of it.
- The slash command (12.03) — independent.
- Any new endpoint (12.02 owns endpoints).

## Implementation Notes
- **`services_render.render_project_service_prose` is pure** — takes a `ProjectService` + the terms-data dict; no I/O at call time. The terms-data dict is loaded once via `@functools.cache` keyed on the file path; the module exposes `get_terms_data() -> dict` so tests can monkeypatch the path or inject a fake.
- **Render boundary is the repository, not the LLM input** — the LLM never sees field-label strings because they never exist in the rendered text. This is the hard structural guarantee FR-25 relies on (the prompt rule is a soft nudge on top).
- **Multi-window rendering** — for `working_hours_json` `{"mon":[["10:00","13:00"],["14:00","18:00"]]}`, render as `"пн 10:00–13:00, 14:00–18:00"`. Use the en-dash `–` (U+2013) in time ranges (NOT the hyphen) to match the FR-25 example exactly.
- **Day grouping (stretch)** — try a simple consecutive-range collapse: if days `["mon","tue","wed","thu","fri","sat"]` all share the SAME windows, render as `"пн–сб 10:00–19:00"`. If grouping introduces correctness risk (mixed-windows days), fall back to per-day enumeration. Pin which form ships in the test fixture.
- **Lemma dedup tokenization unit** — digest text is tokenized into **sentences** (split on `[.!?]\s+`); each sentence's lemmas are computed via `RussianNormalizer.lemmas`. The lemma set comparison `name_lemmas ⊆ sentence_lemmas` is robust to inflection ("маникюр" matches a sentence mentioning "маникюра"). DO NOT use raw string `in` — that misses inflected mentions and over-keeps the digest sentence.
- **Trace `source_id` literal** — write it exactly as `project_services:<project_id>` / `catalog_digest:<project_id>` / `merged:<project_id>` (matches FR-25 acceptance criteria literally; downstream `answer_traces` queries depend on this format).
- **Brownfield continuity** — a project with empty `project_services` (e.g. one that only ever used `/kb_add` PDF uploads) hits the structured-empty + digest-non-empty branch → digest-only render → `source_id` `catalog_digest:<project_id>` (exactly the pre-Epic-12 behavior).
- **No-label-leak invariant** — add a regression test that asserts the customer-visible answer for any catalog question contains none of `Название:`, `Описание:`, `Цена:`, `Длительность:`, `Дни:`, `Часы:`. Run this against fixtures covering structured-only, digest-only, and merged paths.
- **`grounding_system` rule wiring** — write through the existing `project_prompt_repository` so the rule participates in the existing per-project prompt resolution (an operator-edited project prompt overrides it; the default path picks it up). The migration step calls `repo.upsert(project_id=None, slot='grounding_system_rule_fr25', text=<rule>)` (or whatever the repository's "global default" semantics is); idempotent on second boot.
- **Catalog-question intent detection** — re-use the existing catalog-branch detection in `GroundedRagAnswerer` (no new intent classifier needed); the merge happens INSIDE that branch only.

## Test Plan

### Unit
- `tests/test_services_render_prose.py`:
  - Full row (`name + duration + working_hours_single_window + price + description`) → `"Маникюр — 60 минут, пн 10:00–19:00, цена от 2000 ₽. Классический и аппаратный."` (or with day-grouped form if multi-day same-window — pin form).
  - Name-only row → `"Маникюр."`.
  - Name + price_text only → `"Маникюр — цена от 2000 ₽."`.
  - Multi-window day → `"пн 10:00–13:00, 14:00–18:00"`.
  - Date exceptions → `"закрыто: 1 января, 9 мая"`.
  - **No label leak:** `"Название:" not in output`, `"Описание:" not in output`, `"Цена:" not in output`, `"Длительность:" not in output`, `"Дни:" not in output`, `"Часы:" not in output` for ALL fixture rows. **(Story-level explicit acceptance: no field labels in output.)**
  - Terms-data load is cached (same dict instance returned across two calls).
- `tests/test_services_catalog_merge.py`:
  - Structured empty + digest empty → returns `("", "")` (or sentinel); caller treats as `catalog_empty`.
  - Structured empty + digest non-empty → returns `(digest_text, "catalog_digest")`.
  - Structured non-empty + digest empty → returns `(<structured prose>, "project_services")`.
  - Both non-empty, NO overlap → returns `(<structured>\n<digest>, "merged")`.
  - Both non-empty, lemma-overlap (digest mentions "маникюра" + structured row name "маникюр") → digest sentence dropped; structured prose retained; suffix `merged`.
  - Degenerate (empty name lemmas) → no dedup; both kept; suffix `merged`.
  - **Single-row insert does not silently shrink:** structured = 1 row (`маникюр`) + digest mentions 12 services (`маникюр` + 11 others) → output contains 12 service names (1 from structured + 11 unmatched from digest); suffix `merged`.
- `tests/test_russian_hedges_audit.py`:
  - `data/russian_hedges.txt` contains none of: `"от 2000 ₽"`, `"от 60 минут"`, `"от 2000"`, `"от 60"`.

### Contract
- `tests/test_grounded_rag_catalog_branch_contract.py` (with fake `ProjectServiceRepository` + fake `CatalogDigestService`):
  - Structured-only: 1 row → `answer_traces.source_id == "project_services:1"`; answer contains the name.
  - Digest-only: empty repo, non-empty digest → `source_id == "catalog_digest:1"`; answer is the digest (existing behavior).
  - Both: lemma overlap → `source_id == "merged:1"`; structured prose wins on conflict.
  - **No label leak** (cross-check): every contract test asserts none of the 6 label substrings appear in the customer-visible answer.
  - **General "какие услуги?" returns names only** — for a fixture with 3 structured rows each carrying `price_text` + `description`, the answer contains all 3 names but contains NO `price_text` value and NO `description` value (the LLM is asked + the prompt rule shapes the answer; the test asserts the rendered LLM input is the merged prose, AND a follow-up assertion on the captured LLM output mock confirms the names-only shape).
  - **Single-service "сколько стоит маникюр?" includes at most one price + one description** — for a fixture with 3 structured rows, the answer contains only the `маникюр` row's `price_text` and `description`; no other services' prices/descriptions surface.
- `tests/test_grounding_system_rule_migration.py`:
  - First boot writes the FR-25 rule into the prompt repository; second boot is a no-op (existence check); rule text exactly matches the FR-25 specification.

### Integration
- `tests/test_grounded_rag_catalog_branch_integration.py` — boot the answerer with a real SQLite `project_services` repo (1 row) + a real `CatalogDigestService` returning a real-ish digest paragraph; assert end-to-end the `answer_traces` row has the `merged:1` `source_id` and the answer is grounded prose.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_catalog_answer.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-06")`): boot api against a fresh `.data/`; case (a) **brownfield**: project with empty `project_services` + a pre-seeded digest → customer asks "какие услуги?" → answer matches the digest content + `source_id` is `catalog_digest:<id>`. Case (b) **structured-only**: insert 3 service rows; ensure digest is empty for this project → customer asks "какие услуги?" → answer contains all 3 names, NO label substrings, `source_id` `project_services:<id>`. Case (c) **merged with overlap**: 3 structured rows + a digest mentioning 5 services (3 overlap + 2 unique) → "какие услуги?" returns 5 names (3 structured + 2 digest-only); `source_id` `merged:<id>`. Case (d) **single-service question bounded**: "сколько стоит маникюр?" → answer surfaces only маникюр's price_text/description; no other services' details appear.

## Manual Verification
1. Pre-seed a project with 3 service rows (via slash command from 12.03) — `маникюр`, `педикюр`, `стрижка`.
2. Customer asks "какие услуги?" → bot replies with all 3 names, no prices, no descriptions, no field labels.
3. Customer asks "сколько стоит маникюр?" → bot replies with маникюр's price (and only маникюр's).
4. Disable calendar for the project (`POST /calendar/projects/{id}/disable`) → "какие услуги?" still works (catalog branch is independent of calendar enablement).
5. On a project that has only ever used `/kb_add` PDF uploads → "какие услуги?" returns the digest content (brownfield continuity).
6. Check `answer_traces.source_id` after each query → matches the expected `project_services:<id>` / `catalog_digest:<id>` / `merged:<id>` literal.

## Done Criteria
- 100% coverage on `services/api/app/services_render.py`, `services/api/app/services_catalog_merge.py`, the extended catalog branch in `services/api/app/answerers/grounded_rag.py`, and the `grounding_system` rule migration helper.
- `ruff check .` passes.
- **No-field-labels acceptance** verified by an explicit test asserting none of `Название:`, `Описание:`, `Цена:`, `Длительность:`, `Дни:`, `Часы:` appear in the customer-visible answer for ANY fixture path (structured / digest / merged).
- **General "какие услуги?" returns names only** — verified by contract test (multi-row fixture, output contains all names but no prices/descriptions).
- **Single-service question bounded** — verified by contract test (3-row fixture + "сколько стоит маникюр?" surfaces at most one price + one description).
- **Trace `source_id` literal** — verified for all four branches.
- **Brownfield continuity** — empty `project_services` + non-empty digest yields the same content profile as pre-Epic-12 (digest-only path; `source_id` `catalog_digest:<id>`).
- **Single-row insert does not silently shrink** — 1 row + 12-service digest yields 12 names (via merge-with-dedup); `source_id` `merged:<id>`.
- **`grounding_system` rule migration** is idempotent.
- **Hedges audit** passes (no false-positive price/duration phrasings in `data/russian_hedges.txt`).
