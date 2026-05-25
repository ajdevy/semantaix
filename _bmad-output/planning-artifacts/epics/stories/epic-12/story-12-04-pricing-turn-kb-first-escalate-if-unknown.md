# Story 12.04 — Pricing turn: KB-first, escalate-if-unknown

## Objective
Add the `pricing` stage to `SalesPersonaAnswerer`: on a customer price ask, query the RAG knowledge base for a matching price; if found, quote it verbatim with `source_id` in the answer trace; if missing, escalate to HITL with `reason='price_unknown'` and reply "Уточню у коллег и сразу сообщу". The operator's HITL reply is delivered to the customer **and** picked up by the existing Epic-06 `knowledge_moderation` extractor — meaning the **next** identical price ask hits the KB without escalating. This is the "bot learns prices over time" loop, with zero new ingestion code in this epic.

## Scope

### In Scope
- `services/api/app/sales/price_lookup.py` `PriceLookup(*, rag_retriever, normalizer)`:
  - `async def lookup(*, project_id, intent: Intent, question: str) -> PriceFound | PriceMissing`.
  - Builds a lemma-space query from `intent.service_name`, `intent.vehicle_type` (derived from the picked service tag — see notes), `intent.hours`, plus `"цена"` / `"стоимость"` / `"₽"` anchors.
  - Calls `rag_retriever.retrieve(query, project_id=...)` (the existing lemma-overlap retriever used by `GroundedRagAnswerer`).
  - Scores candidates: a chunk is a "price hit" only if it contains a digit-then-currency token (`\d[\d\s]*(?:₽|руб|р\.|RUB)`) or the explicit phrase shape "<number> — <currency>". This guardrail keeps the bot from quoting a non-price chunk that happens to mention the service.
  - On hit: returns `PriceFound(text=chunk.text, source_chunk_id=chunk.id, snippet=chunk_snippet_around_price)`.
  - On miss (no chunks, or none with a price token): returns `PriceMissing(question_for_operator=structured_payload)`.
- Frozen dataclasses `PriceFound(text: str, source_chunk_id: str, snippet: str)` and `PriceMissing(payload: PriceUnknownPayload)`; `PriceUnknownPayload(service: str | None, vehicle_type: str | None, hours: int | None, original_question: str)`.
- Pricing stage in `SalesPersonaAnswerer` (the `pricing` branch previously stubbed in 12.03):
  - Entered when the customer asks a price question while in `scoping` (intent regex `цена|стоимость|сколько (стоит|это|за)|почём`) OR explicitly via stage transition after `scoping → pitching → pricing` (the natural flow).
  - Calls `price_lookup.lookup(...)`:
    - **Hit:** generates a Russian reply via `system_prompts/nikolay_pricing_hit.txt` (constraint: must include the quoted number + currency verbatim from `PriceFound.snippet`; one sentence; no hedging). Records `source_chunk_id` in the `answer_traces` metadata via `ctx.trace_metadata["sales_price_source_chunk_id"] = ...`. Stays in `pricing` (the customer may ask another price).
    - **Miss:** creates a HITL ticket via the existing flow with `reason='price_unknown'` and `metadata={"sales_price_unknown_payload": payload.as_dict()}`; the ticket is routed to the project's primary operator. The customer-facing reply is the fixed Russian line `"Уточню у коллег и сразу сообщу"` (no LLM call). Transitions stage to `awaiting_operator_price` (a new pseudo-stage that holds the funnel until the operator replies; on customer message in this stage, treats it as the next scoping/pricing turn).
- Persona prompt files:
  - `system_prompts/nikolay_pricing_hit.txt` — one-sentence Russian quote with the verbatim number; persona-name placeholder; price-as-data instruction ("ONLY quote numbers present verbatim in the snippet — never round, infer, or convert currency").
- **No new ingestion code.** The operator's HITL reply (when they answer the `price_unknown` ticket) flows through the existing Epic-06 `knowledge_moderation` extractor unchanged. This story verifies (via a single integration test) that the extractor picks up the reply transcript line, given the `reason='price_unknown'` tag — **if** that requires a one-line extension to the Epic-06 extractor (e.g. broadening which reply kinds get scanned), the patch ships as a follow-up to Epic 06, not as code in 12.
- Sales-intent regex (`data/russian_sales_intent.txt`) extended with the price-ask lemmas (`цена`, `стоимость`, `сколько`, `почём`) — additive update, no code change to the loader.

### Out of Scope
- Discount handling (epic out-of-scope; discount asks fall through to HITL via `low_confidence`).
- Currency conversion or arithmetic on the quoted price.
- Multi-currency support (RUB only; the price-token regex includes `₽|руб|р.|RUB` and nothing else in v1).
- Pricing-history audit log (the answer trace + the HITL ticket are the audit trail).
- A "price suggestion" turn where the bot proposes a higher tier — explicit out-of-scope.

## Implementation Notes
- **Price quoting is a quoted span, not LLM-paraphrased.** The hit prompt receives `snippet` (a ±60-char window around the price match) and instructs the model to reuse the exact number-and-currency token. Verifier guardrail: after LLM generation, regex-extract the number from the snippet and assert it appears verbatim in the reply; on mismatch → log `sales_price_quote_drift` and escalate.
- **`PriceUnknownPayload.as_dict()`** is the structured ticket metadata. Customer's verbatim question goes in `original_question` per the existing HITL contract; never paraphrase it.
- The `awaiting_operator_price` pseudo-stage lives in the `current_stage` enum (`{new, scoping, pitching, pricing, awaiting_operator_price, proposing, closing, dormant}`) — update the 12.01 enum docstring accordingly. On the next customer message in this stage, the answerer re-enters `pricing` (the bot doesn't loop on "уточняю..."); the operator's actual price reply is delivered by the existing HITL reply path, not by this answerer.
- **No fabricated prices.** If the LLM returns text whose price token doesn't match the snippet, escalate — never send a possibly-wrong price.
- **One RAG call per price ask.** No re-tries on miss (the customer-facing line is fixed). On `rag_retriever` exception → `self._skip(reason="rag_unavailable")` (which falls through to RAG/HITL via the standard path).

## Test Plan
### Unit
- `tests/test_sales_price_lookup.py` — query construction from `Intent`; price-token regex matches `15 000 ₽`, `15000 руб`, `5 000 р.`, `15000 RUB`; non-price chunk excluded; multiple hits → first with a price token wins; `PriceMissing.payload` carries the verbatim question.
- `tests/test_sales_persona_answerer_pricing_hit.py` — fake LLM returns a one-sentence reply containing the verbatim price; answerer sets `ctx.trace_metadata["sales_price_source_chunk_id"]`; stage stays `pricing`.
- `tests/test_sales_persona_answerer_pricing_quote_drift.py` — LLM returns a reply whose number doesn't match the snippet → answerer logs `sales_price_quote_drift` and escalates (no customer-visible wrong price).
- `tests/test_sales_persona_answerer_pricing_miss.py` — empty RAG → answerer creates a HITL ticket with `reason='price_unknown'` and `metadata.sales_price_unknown_payload` populated; customer-facing reply is the fixed `"Уточню у коллег и сразу сообщу"`; stage transitions to `awaiting_operator_price`.
- `tests/test_sales_persona_answerer_pricing_rag_unavailable.py` — `rag_retriever.retrieve` raises → `_skip(reason="rag_unavailable")`.

### Integration
- `tests/test_sales_pricing_kb_learning_loop.py` — full loop against real `StateRepository`, real `HITLRepository`, and a stubbed `knowledge_moderation` extractor: (1) customer asks price → empty KB → ticket created with `reason='price_unknown'`; (2) operator's HITL reply line `"6 часов — 15 000 ₽"` is processed by the existing extractor → moderation candidate exists; (3) **simulate operator-approves** by directly inserting the resulting `rag_chunks` row; (4) customer asks the same price again → `PriceFound` returned, no new ticket.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_pricing_loop.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-04")`) — scripted end-to-end of the loop above through `/conversations/inbound` + the operator HITL reply path + the moderation-candidate approval endpoint, asserting the second ask returns a quoted price without a ticket.

## Manual Verification
1. Seed the project with `/service_add Медовеевка Лайт`.
2. As a customer: complete scoping, then ask `"Сколько стоит 6 часов?"` — expect `"Уточню у коллег и сразу сообщу"` + a HITL ticket appears in the operator DM.
3. As the operator: reply to the ticket with `"6 часов — 15 000 ₽"`. Confirm the customer receives the reply.
4. In the web UI (Epic-08 knowledge moderation surface), confirm a moderation candidate appears; approve it.
5. As the customer: ask the same question again — expect a direct quote `"6 часов — 15 000 ₽"` without an operator escalation.

## Done Criteria
- 100% coverage on `price_lookup.py` and the new `pricing` / `awaiting_operator_price` branches of `sales_persona_answerer.py`.
- `ruff check .` passes; E2E green.
- Quoted prices match the source snippet verbatim (regex-asserted in the unit test); drift escalates instead of replying.
- HITL ticket carries `reason='price_unknown'` + the structured payload; customer's verbatim question preserved.
- No new ingestion code in this epic; the Epic-06 extractor handoff is verified by integration test (any extractor tweak ships as a follow-up to Epic 06, not as code here).
- Customer-facing reply on miss is the fixed Russian line, never an LLM call.
