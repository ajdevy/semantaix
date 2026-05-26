# Story 12.07 — Date-proposal turn via Epic 11 calendar

## Objective
After scoping + pricing reach a natural close, the bot proposes a specific calendar slot: "Предлагаю на 1 число с началом в 14:00." The proposal comes from `compute_availability()` (Epic 11) — never from a free-form LLM call — and is persisted into `state.last_proposal` so the bot can refer to it on follow-up turns.

## Scope

### In Scope
- `services/api/app/sales/date_proposer.py` `DateProposer(*, availability_compute, services_repo, settings_repo, normalizer, clock)`:
  - `async def propose(*, project_id: int, intent: Intent, now: datetime) -> Proposal | NoProposal`.
  - Resolves the candidate service: `services_repo.find_by_name(project_id, intent.service_name)` (or fall back to the only-active service if exactly one exists; ambiguous → `NoProposal(reason="ambiguous_service")`).
  - Translates `intent.dates` (a free-text Russian date span like "1–3 мая" or "1 мая") into a `(start_date, end_date)` window via a small parser in `services/api/app/sales/date_parser.py` (`parse_russian_date_span(text, *, now)` returning `(date, date) | None`). Reuse `RussianNormalizer` for tokenization; reuse `holidays` only via Epic-11's existing engine (no new holiday library calls here).
  - Calls Epic-11's `compute_availability(project_id=..., service_id=..., window=..., hours=intent.hours, now=now)` — the existing pure function — and picks the earliest slot that fits.
  - Returns `Proposal(date_iso, start_time_iso, end_time_iso, service_id, slot_source="epic11_availability")` on success; `NoProposal(reason: str)` otherwise (no service, ambiguous, no calendar enabled, no slots in window, calendar provider unreachable).
- Frozen dataclasses `Proposal` and `NoProposal`.
- `SalesPersonaAnswerer` integration:
  - **Entry condition for the `proposing` stage:** transition from `pricing` happens when (a) at least one price ask has been answered (hit or learned), AND (b) `intent.dates` is populated. Otherwise the answerer asks the missing piece (the existing scoping behavior).
  - In `proposing` stage: call `date_proposer.propose(...)`.
    - **`Proposal`** → render via `system_prompts/sales_proposal.txt` (Russian, one sentence, includes the date + time as digits, no hedging). Persist into `state.last_proposal` via `StateRepository.upsert(..., last_proposal=proposal.as_dict())`. Stay in `proposing` (the customer may accept, decline, or counter).
    - **`NoProposal(reason="ambiguous_service")`** → fall back to a one-line scoping clarifier ("На каком туре остановимся?"); stage stays `proposing`.
    - **`NoProposal(reason="calendar_not_enabled")`** → reply with a fixed Russian line "Дату подтвержу у коллег" and escalate to HITL with `reason='date_calendar_disabled'`. The funnel does NOT silently go quiet — it always tells the operator a date confirmation is pending.
    - **`NoProposal(reason="provider_error" | "no_slots_in_window")`** → same escalation pattern; the customer-facing line is "Уточню свободные даты и сразу сообщу".
- `system_prompts/sales_proposal.txt` — Russian, persona-aware, one short sentence, format: `Предлагаю на <дата> с началом в <время>.` The prompt MUST include the constraint "только цифры, без 'около'/'примерно'" (no fuzzy times — the calendar gave us an exact slot).
- Stage transition out of `proposing`:
  - Customer accepts (intent: `да|согласен|подтверждаю|давайте|устраивает`) → transition to `closing`; the actual booking remains with the operator per epic out-of-scope.
  - Customer declines / counter-offers (free-text date) → re-enter `proposing` with updated `intent.dates`.
  - In `closing`, the answerer says a closing line ("Передам коллегам для подтверждения, на связи.") and escalates to HITL with `reason='sales_closing_handoff'`.

### Out of Scope
- Actual booking / event creation in the operator's calendar (Epic 11 is read-only; booking is a future epic).
- Alternative-slot suggestions when the first proposal is declined (v1 re-proposes from the customer's new date hint; the bot does not enumerate alternatives unprompted).
- Multi-service date packaging ("1 мая каньонинг + 2 мая квадро") — v1 proposes one service per turn.
- Date parsing beyond the simple Russian span shapes (`<число> <месяц>`, `<число>–<число> <месяц>`). Edge forms like "следующая суббота" fall through to scoping clarification.
- Time zone conversion at the customer's end — the proposal is in the project timezone (already a property of Epic 11's `compute_availability`).

## Implementation Notes
- **Date proposals never come from the LLM.** The proposal sentence is rendered by a prompt that receives `{date, start_time}` as fixed substitution values; the LLM's role is grammar / phrasing only. Verifier guardrail: regex-extract `(\d{1,2}\s+\w+)` and `(\d{1,2}:\d{2})` from the reply, assert they match the `Proposal` values exactly; mismatch → log `sales_proposal_drift` and escalate.
- **`compute_availability` is reused as-is.** This story does not modify Epic 11. If the per-service rules need a new shape (e.g. multi-hour bookings), that's a follow-up to Epic 11, not new code here.
- **Calendar-not-enabled is a normal path, not an error.** `settings_repo.is_enabled(project_id)` returns False → `NoProposal(reason="calendar_not_enabled")`. The escalation path is the only way the customer learns the date is pending.
- **`Proposal.as_dict()`** is the persisted shape in `state.last_proposal`. Includes `proposed_at` (ISO-8601 UTC) so a stale proposal can be re-confirmed by the operator without round-tripping.
- **Russian date span parser is small and tested.** Months: `января...декабря` + nominative (`май`) and prepositional (`в мае`) forms — reuse `pymorphy3` lemmas (`месяц`). Two patterns: `(\d+)\s+(месяц)` and `(\d+)[–-](\d+)\s+(месяц)`. Reject anything else with `None` (the answerer asks for clarification).
- **Customer-acceptance detection is conservative.** The acceptance lemma list lives in `data/russian_sales_acceptance.txt` (new file): `да`, `согласен`, `согласна`, `подтверждаю`, `давайте`, `устраивает`, `ок`, `хорошо`, `замечательно`. Match by lemma overlap; no LLM call for acceptance detection.

## Test Plan
### Unit
- `tests/test_sales_date_parser.py` — parses `"1 мая"`, `"1–3 мая"`, `"1-3 мая"`, `"в мае"`, `"15 июня"`; rejects `"следующая суббота"`, `"скоро"`, empty.
- `tests/test_sales_date_proposer_proposal.py` — `compute_availability` returns a slot → `Proposal` with the expected `(date, start, end)`; persists nothing (the answerer is the one that calls `state_repo.upsert`).
- `tests/test_sales_date_proposer_no_proposals.py` — covers each `NoProposal.reason`: `ambiguous_service` (two active services + ambiguous intent), `calendar_not_enabled`, `provider_error` (mock raises), `no_slots_in_window`.
- `tests/test_sales_persona_answerer_proposing_hit.py` — `Proposal` → bot renders the Russian sentence with verbatim values; `state.last_proposal` persisted.
- `tests/test_sales_persona_answerer_proposing_drift.py` — LLM returns a sentence with the wrong time → escalates with `reason='sales_proposal_drift'`, no customer-visible wrong date.
- `tests/test_sales_persona_answerer_proposing_calendar_disabled.py` — `NoProposal(reason="calendar_not_enabled")` → fixed Russian fallback + HITL ticket with `reason='date_calendar_disabled'`.
- `tests/test_sales_persona_answerer_acceptance.py` — customer reply matching an acceptance lemma → transition to `closing` + closing line + HITL handoff ticket; non-acceptance reply with a new date → re-enter `proposing` with updated intent.

### Integration
- `tests/test_sales_proposing_round_trip.py` — two turns: bot proposes a date, customer counter-offers a new date, bot re-proposes from the new window.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_date_proposal.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-07")`):
  - Seed services + a price RAG chunk + enable Epic-11 calendar with a stub that returns a free slot for `1 мая 14:00`.
  - Customer dialog: scoping → price ask (hit) → date hint ("1 мая") → bot proposes `1 мая 14:00`; customer accepts → closing line + HITL ticket with `reason='sales_closing_handoff'`.
  - Negative path: same setup but Epic-11 disabled → `"Дату подтвержу у коллег"` + HITL `reason='date_calendar_disabled'`.

## Manual Verification
1. Connect a Google Calendar via `/connect_calendar` (Epic 11 path); confirm a free slot on `1 мая 14:00`.
2. As a customer: complete scoping, ask a price (seed it via `/kb_add` or rely on the price-learning loop), then say "1 мая" — expect `"Предлагаю на 1 мая с началом в 14:00."`
3. Reply "да, согласен" — expect `"Передам коллегам для подтверждения, на связи."` and a HITL ticket in the operator DM with `reason='sales_closing_handoff'`.
4. Disable the calendar (`/calendar_off`) and re-run the date turn — expect the calendar-disabled fallback + escalation.

## Done Criteria
- 100% coverage on `date_proposer.py`, `date_parser.py`, and the new `proposing` / `closing` branches of `sales_persona_answerer.py`.
- `ruff check .` passes; E2E green.
- Proposal values match `compute_availability`'s output verbatim (drift escalates).
- `state.last_proposal` persisted with `proposed_at`.
- Acceptance detection is lemma-based (no LLM); list lives in `data/russian_sales_acceptance.txt`.
- Calendar-disabled and provider-error paths both escalate with a fixed Russian fallback (no silent funnel halt).
- No modification to Epic 11's `compute_availability`.
