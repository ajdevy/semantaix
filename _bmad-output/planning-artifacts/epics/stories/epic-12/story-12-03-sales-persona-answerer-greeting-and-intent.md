# Story 12.03 — `SalesPersonaAnswerer` greeting and intent scoping

## Objective
Stand up the `SalesPersonaAnswerer` skeleton with the greeting + intent-scoping stages. The answerer detects sales intent (or picks up an existing `sales_conversation_state` row), gathers the five intent fields the Дарья dialog establishes (`dates`, `headcount`, `vehicle_count`, `difficulty`, `drivers`), persists state across turns, and emits Russian replies under the "Николай" persona. **This story does NOT wire the answerer into the pipeline** (12.09 does that) — it ships the answerer module, the intent regex data file, and the per-stage system prompts in isolation, tested end-to-end against a fake LLM.

## Scope

### In Scope
- `services/api/app/sales/sales_persona_answerer.py` `SalesPersonaAnswerer` (constructor-injected deps: `state_repo: StateRepository`, `services_repo: ServicesRepository`, `openrouter: OpenRouterClient`, `normalizer: RussianNormalizer`, `clock` returning aware `datetime`, `bot_persona_getter` callable returning the configured persona name):
  - Implements the `Answerer` Protocol: `async def try_answer(self, *, question: str, ctx: AnswerContext) -> AnswerResult`.
  - `name = "sales_persona"` (class attr).
  - **Activation gate (always-on, cheap, first):**
    1. `state = state_repo.get(ctx.chat_id)` — if exists and `current_stage != 'dormant'` → continue with that state.
    2. Else: run the sales-intent regex on `normalizer.lemmas(question)`; non-match → `self._skip(reason="not_sales_intent")`.
    3. Match → enter the greeting stage. **No services-count check.** Sales is enabled for every project; an empty `services` catalog is a valid state — the bot can still scope, ask for media (none → no-op), look up prices via RAG, and propose calendar dates.
  - **Stage routing:** dispatch on `state.current_stage` (`new`, `scoping`); other stages (`pitching`, `pricing`, `proposing`, `closing`) are reserved for later stories and `_skip(reason="stage_not_implemented_yet")` for v1 of this story — only greeting + scoping ship here.
  - **Greeting stage** (`new` → `scoping`): on first sales-intent message, generate a greeting under the "Николай" persona using `system_prompts/nikolay_greeting.txt`. Handles referral phrases ("контакт передали из Хиллс") — the prompt includes a referral-detection instruction. Asks the first scoping question (date). Transitions to `scoping` and persists.
  - **Scoping stage** (loops in `scoping` until all 5 fields collected → transitions to `pitching`): merges newly-extracted fields into `collected_intent` JSON, asks the next missing field as a Russian one-liner. Order: `dates → headcount → vehicle_count → difficulty → drivers`.
  - **LLM extraction is structured.** Each turn calls the LLM with a fixed JSON-out schema (`{extracted_fields: {...}, next_question: "..."}`) so the answerer can deterministically merge `extracted_fields` into state without parsing free-form text. Reject + escalate on schema-violation.
  - **State persistence:** every turn that returns `handled=True` calls `state_repo.upsert(...)` with merged intent, current stage, and `last_bot_msg_at = clock()`. Customer-message timestamp is set by the pipeline (or by 12.08's queue cancel), not by the answerer itself.
- New data file `data/russian_sales_intent.txt` — one phrase per line (lemma form): `тур`, `квадро`, `квадроцикл`, `багги`, `эндуро`, `прокат`, `катан`, `маршрут`, `цена`, `стоимость`, `сколько стоит`, `даты`, `мая`, `мая числа`, ... — loaded at startup by a new `russian_sales_intent.py` module that wraps `RussianNormalizer.lemmas(question)` overlap matching (mirror the existing `russian_hedges.txt` load pattern). EN entries optional. **The list is data, not Python literals.**
- `services/api/app/sales/system_prompts/nikolay_greeting.txt`, `nikolay_scoping.txt` — Russian persona prompts. Greeting prompt includes: tone (warm, professional, concise), the persona name placeholder (filled at runtime from `bot_persona_getter`), referral-detection instruction (if the user mentions a referral source, acknowledge it in the first sentence), and the JSON-out contract. Scoping prompt is the same shape, parameterized by `missing_fields`.
- New frozen dataclass `Intent` (already declared in 12.01) exposed via a typed merge helper `intent_merge(existing: Intent, extracted: dict) -> Intent` in `services/api/app/sales/intent.py` — never blindly overwrites a populated field with `None` from the new turn.
- `SalesPersonaAnswerer` constructor is wired in `services/api/app/main.py` but the answerer is **not yet inserted** into the `AnswerPipeline` list (12.09 inserts it). Construction is gated behind a feature flag-free conditional that is always true once 12.01 lands — the dormant-by-default behavior comes from the activation gate, not from a flag.

### Out of Scope
- Pipeline insertion (12.09).
- Pricing stage (12.04), service-list / concept-explainer (12.06), date proposal (12.07), follow-up (12.08), media dispatch (12.05).
- Discount handling (epic out-of-scope).
- Any LLM-driven service recommendation / cross-sell — the scoping prompt does NOT pitch a service; pitching is for the next story.

## Implementation Notes
- **Answerers DISPATCH, they don't error** (project-context). All `_skip(reason=...)` returns are silent fall-throughs to the next answerer. The only `handled=True` returns are the greeting and the next-scoping-question turns.
- **LLM call discipline:** one structured JSON-out call per turn via `OpenRouterClient.complete_json(...)` (extend the existing client signature if needed to enforce a response_format). On JSON-schema violation → log `sales_llm_schema_violation` + `self._skip(reason="llm_schema_violation")` so the message escalates to RAG/HITL via the existing fall-through.
- **Time is injected** (`clock`) — never `datetime.now()` inside the class. Required for the 100% gate on the per-stage timestamp branches.
- **Russian-first data** — intent phrases live in `data/russian_sales_intent.txt`, never as Python literals. `RussianNormalizer.lemmas` is the only tokenizer used (no parallel intent detector).
- **Persona name is configurable** — read via `bot_persona_getter` (which wraps `hitl_runtime_config.get_bot_persona()`); never hard-code "Николай".
- **Structured logging:** every turn logs `sales_answerer_handled` with `{trace_id, stage_before, stage_after, fields_extracted}`. Never log the raw LLM output verbatim (may contain customer PII the answerer didn't catch).
- **Intent extraction must be conservative.** If the LLM returns `extracted_fields: {"dates": "1 мая"}` for a turn that said `"да"`, the JSON-out contract instructs the LLM to leave fields it didn't see as absent (NOT `null`). `intent_merge` ignores absent keys.
- **Per-stage prompts cap the response length** (one or two short sentences) — embed the constraint in the prompt; the regex guardrails from Epic-03 are not in this story's scope but still run downstream when the answerer is wired.

## Test Plan
### Unit
- `tests/test_sales_intent_loader.py` — `data/russian_sales_intent.txt` loads and trims; an inbound message matching at least one lemma → `True`; non-match → `False`.
- `tests/test_sales_intent_merge.py` — `intent_merge` overwrites empty fields, preserves populated fields when the new turn doesn't mention them, replaces a populated field only when the new turn explicitly carries a new value, never propagates `None` over a populated field.
- `tests/test_sales_persona_answerer_gate.py` — activation gate (always-on): existing state + non-dormant → enters regardless of services count; no state + non-sales text → `_skip(reason="not_sales_intent")`; no state + sales text → enters greeting even on a project with **zero** `services` rows (asserted explicitly — sales is never gated by catalog size).
- `tests/test_sales_persona_answerer_greeting.py` — greeting turn with a fake LLM returning a fixed JSON: produces the expected Russian greeting, transitions `new → scoping`, persists state with the merged intent; referral phrase ("контакт передали из Хиллс") causes the prompt to include the referral source — verified by inspecting the captured prompt args on the mocked client.
- `tests/test_sales_persona_answerer_scoping.py` — five-turn scoping run with a scripted LLM, asserts the expected question order and that `collected_intent` ends fully populated and the stage transitions to `pitching` (which immediately `_skip(reason="stage_not_implemented_yet")` for this story).
- `tests/test_sales_persona_answerer_llm_schema_violation.py` — LLM returns invalid JSON → answerer logs + skips, does not raise into the pipeline.

### Integration
- `tests/test_sales_persona_answerer_state_roundtrip.py` — two calls in sequence against a real `StateRepository` (tmp DB): first call greets and persists, second call resumes from the persisted state and asks the next scoping question.

## Automated E2E verification
- None for this story (pipeline insertion is 12.09). Unit + integration coverage validates the state machine end-to-end against a fake LLM.

## Manual Verification
- Not user-visible until 12.09 wires the answerer; manual verification deferred to the wiring story.

## Done Criteria
- 100% coverage on `sales_persona_answerer.py`, `intent.py`, the intent-loader module, and the prompt-loading helper.
- `ruff check .` passes.
- `data/russian_sales_intent.txt` present with the initial seed list (the phrases listed under In Scope, plus the obvious "привет"/"добрый день" greeting forms — keep the file ≤ 50 entries in v1; expansion comes via real-traffic observation).
- Persona name read from `bot_persona_getter`, never hard-coded.
- No `datetime.now()` inside the answerer; `clock` injected at construction.
- LLM output schema is enforced; schema-violation logged and skipped without raising.
- Module is constructed in `main.py` startup but **not yet inserted** into `AnswerPipeline` (regression-tested in 12.09's wiring story).
- Activation is **always-on** — no `services` count check, no `/sales_on` command. The only sources of dormancy are an explicit `current_stage='dormant'` on a state row OR a non-sales-intent inbound (which falls through cleanly to the next answerer).
