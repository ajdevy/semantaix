# Story 12.06 — Service-list and concept-explainer turn

## Objective
Handle two natural conversation moments observed in the dialogs:
1. **"Что у вас есть?" / "какие туры":** customer asks for the catalog → bot lists active services by name.
2. **"А что такое X?":** customer asks what something means (e.g. Дарья asking "А что такое каньонинг?") → bot replies with the service's `description_md` if present; falls back to the standard `GroundedRagAnswerer` if not; escalates if neither has an answer.

This story lives entirely inside `SalesPersonaAnswerer` (the `scoping`, `pitching`, and `pricing` stages may all be interrupted by either of these turns — they're conversational asides, not stage transitions).

## Scope

### In Scope
- Intent extension (`data/russian_sales_intent.txt`): add lemma phrases for catalog asks (`что у вас есть`, `какие туры`, `что предлагаете`, `варианты`, `список`) and the "what is X" probe form (`что такое`, `что значит`, `объясните`, `расскажите про`, `расскажите о`).
- Per-turn intent classifier helper in `services/api/app/sales/turn_intent.py`:
  - `classify_turn(question: str, normalizer: RussianNormalizer) -> TurnIntent` where `TurnIntent ∈ {catalog_ask, concept_ask(name), price_ask, scoping_answer, other}`.
  - `concept_ask` extracts the candidate term following `что такое` / `что значит` / etc. via a lemma-anchored span match (the term is whatever follows the trigger phrase up to punctuation or end of sentence).
  - `catalog_ask` is a boolean match on the catalog-lemma list.
  - Returns `other` when nothing matches — the caller (the answerer) decides whether the turn is a scoping answer (in `scoping` stage) or `_skip`.
- `SalesPersonaAnswerer` extension:
  - **At the top of every stage** (after the activation gate, before stage-specific logic): call `classify_turn(...)`. If `catalog_ask` or `concept_ask`, handle the turn inline and return — the funnel state (`current_stage`, `collected_intent`) is preserved across the aside.
  - **`catalog_ask` branch:** `services_repo.list_active(project_id)` → render a Russian list using `system_prompts/sales_catalog.txt`. Format: `У нас есть:\n• Медовеевка Лайт\n• Ивановский водопад\n• Каньонинг\n\nЧто вас интересует?` (no IDs, no descriptions, no prices — the list is just names). Empty list → `_skip(reason="no_services")` (the dormancy gate should already have caught this, but defense-in-depth).
  - **`concept_ask` branch:**
    1. `services_repo.find_by_name(project_id, term)` (case-insensitive). On hit + `description_md` populated → return the description as-is (no LLM rewriting — preserves the operator's intended copy). Stay in the current stage.
    2. On hit but `description_md` is None → call `rag_retriever.retrieve(term + " определение")` scoped to the project. If a high-confidence chunk exists (reuse the existing `rag_grounding_score_threshold`), reply with the chunk text via a one-sentence persona wrapper (`system_prompts/sales_concept_rag.txt`). On low-confidence / no chunk → escalate via the existing HITL path with `reason='concept_unknown'`.
    3. On miss (no matching service) → same RAG-then-escalate path scoped to the term (so the bot can still answer "что такое каньонинг?" even if it's not a service row yet).
- `system_prompts/sales_catalog.txt` and `sales_concept_rag.txt` — Russian, persona-aware, one short reply, no hedging.

### Out of Scope
- Per-service pricing in the catalog list (pricing is handled by the dedicated 12.04 turn).
- Search across `description_md` text (fuzzy or full-text) — v1 is exact name match.
- Multi-service comparison ("чем X отличается от Y") — falls through to RAG/HITL.
- Editing `description_md` after the service is added — same as 12.02, delete + re-add in v1.
- LLM-driven service recommendation when the customer asks "что у вас есть" — v1 lists; pitching is the next conversational move.

## Implementation Notes
- **The catalog list is operator-authored data, not LLM-generated.** The prompt's job is to wrap the list with a Russian preamble + suffix sentence — never to invent names, reorder by perceived appeal, or add hedging ("у нас есть много чего"). Verify in the unit test: the LLM is given the list, and the output contains every name verbatim.
- **Concept hit prefers the operator's description.** If `description_md` exists, return it as-is (no LLM call). This keeps a service like "каньонинг — это…" exactly the way the operator wrote it.
- **RAG fallback uses the existing retriever.** Same threshold (`Settings.rag_grounding_score_threshold`), same lemma-overlap, same project scoping — no parallel retrieval logic.
- **Funnel state is preserved.** A catalog/concept aside in the middle of `scoping` keeps the `collected_intent` intact and `current_stage` unchanged. The next inbound message resumes scoping from where it was.
- **`concept_ask` term extraction is conservative.** If the post-trigger span is empty or only punctuation, classify as `other` (don't try to guess). E.g. "Что такое?" alone → `other` (likely a scoping clarifier).
- **Trace metadata:** every handled turn records `ctx.trace_metadata["sales_turn_kind"] = "catalog" | "concept_op_desc" | "concept_rag" | ...` for downstream auditing in `answer_traces`.

## Test Plan
### Unit
- `tests/test_sales_turn_intent.py` — `classify_turn` matrix: catalog asks, concept asks with valid term, concept asks with empty term → `other`, price asks → `price_ask`, scoping-shaped answers → `other`, no match → `other`.
- `tests/test_sales_persona_answerer_catalog_ask.py` — mid-scoping catalog ask: bot lists names, persists state unchanged, stays in `scoping`; empty services → `_skip(reason="no_services")`.
- `tests/test_sales_persona_answerer_concept_ask_operator_description.py` — `find_by_name` returns a service with `description_md` → reply is the description verbatim, no LLM call (asserted by a mocked LLM having zero invocations).
- `tests/test_sales_persona_answerer_concept_ask_rag_fallback.py` — service exists but description is None → high-confidence RAG hit → persona-wrapped one-sentence reply; low-confidence → escalate with `reason='concept_unknown'`.
- `tests/test_sales_persona_answerer_concept_ask_unknown_service.py` — `find_by_name` miss → RAG retrieval scoped to the term; hit → reply; miss → escalate.

### Integration
- `tests/test_sales_concept_keeps_funnel_state.py` — three-turn run: scoping → concept aside → scoping resumes from the prior `collected_intent` and `current_stage`.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_catalog_and_concept.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-06")`):
  - Seed `/service_add Медовеевка Лайт | Лайт уровень, с видами` and `/service_add каньонинг | Каньонинг — это спуск по верёвке…`.
  - Customer: "Что у вас есть?" → bot lists both names.
  - Customer: "А что такое каньонинг?" → bot returns the operator's description verbatim (no RAG, no LLM rewrite — asserted via answer-trace `sales_turn_kind="concept_op_desc"`).
  - Customer: "А что такое родео?" (no service, empty RAG) → escalate with `reason='concept_unknown'`.

## Manual Verification
1. Seed two services with `/service_add` (one with `| description`, one name-only).
2. As a customer: "Что у вас есть?" → expect a Russian list of names.
3. "А что такое <name with description>?" → expect the operator's description verbatim.
4. "А что такое <name without description>?" → expect a RAG-grounded answer or escalation.
5. "А что такое <unknown term>?" → expect RAG or escalation.

## Done Criteria
- 100% coverage on `turn_intent.py` and the new `catalog_ask` / `concept_ask` branches of `sales_persona_answerer.py`.
- `ruff check .` passes; E2E green.
- Operator-authored descriptions returned verbatim (no LLM rewrite path on the `description_md` hit).
- RAG fallback reuses the existing retriever + threshold (no parallel retrieval).
- Funnel state preserved across asides.
- Catalog list contains every active service name verbatim (no LLM-introduced names or omissions).
