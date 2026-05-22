# Story 11.07 — `CalendarAvailabilityAnswerer` + pipeline wiring

## Objective
Tie the epic together: a `CalendarAvailabilityAnswerer` that implements the `Answerer` Protocol, sits **before** `GroundedRagAnswerer` in the pipeline, enforces the opt-in tri-state gate, orchestrates intent → service-resolve → token → freeBusy → `compute_availability`, answers in Russian, and escalates-on-uncertainty to the calendar operator. Ships the end-to-end availability behavior.

## Scope

### In Scope
- `services/api/app/calendar/availability_answerer.py` `CalendarAvailabilityAnswerer` (constructor-injected deps: `settings_repo`, `token_provider`, `freebusy_client`, `service_resolver`, `normalizer`, `incident`/escalation hooks, `clock`):
  - **Gate (cheap, first):** `to_thread(settings_repo.is_enabled, project_id)`; not enabled → `handled=False` immediately (no intent work, no API call).
  - **Intent:** reuse the existing scheduling-intent regex; non-scheduling → `handled=False`.
  - **Service resolve (11.06):** `NoMatch`/`Ambiguous`/no-time → one clarifying turn (return a `handled` Russian clarifying message, recording a minimal one-turn state); on the follow-up still unresolved → escalate.
  - **Connected?:** if no token / status `reconnect_needed` → "calendar isn't connected yet" path and/or escalate (never 500).
  - **Compute:** `token_provider.get_access_token` → `freebusy_client.query_busy` (one call) → `compute_availability` in project tz → Russian available/not-available answer.
  - **Failure:** `CalendarReconnectNeeded` / `CalendarProviderError` / timeout → escalate to HITL **routed to the calendar operator** with context; never a fabricated answer.
- Pipeline wiring in `services/api/app/main.py`: insert `CalendarAvailabilityAnswerer` **before** `GroundedRagAnswerer` in the `AnswerPipeline` list.
- `scripts/epic11_signoff.sh` + `e2e-coverage.md` rows.

### Out of Scope
- Booking, alternative-slot suggestions, multi-operator/multi-calendar (deferred per epic).
- Changing the four-layer grounding pipeline beyond inserting this answerer ahead of it.

## Implementation Notes
- **Answerers DISPATCH, they don't error** (project-context): "not my intent" / disabled → `handled=False`; "my intent but degraded" → escalate (a *handled* HITL outcome), never silently fall through to RAG.
- Escalation reuses the existing HITL path (Epic 04) but **routes to the project's calendar operator** and includes context "availability question; calendar error/uncertainty". Confirm routing override is supported or extend minimally.
- The one-turn clarify state must be lightweight and scoped to the conversation; if implementing turn state is heavy, escalate immediately on ambiguity for v1 (decision recorded — but the preferred path is clarify-once).
- All customer-facing copy is Russian (configurable as data); structured logs carry `trace_id`, never tokens/event content.
- Never echo calendar event titles — only free/busy-derived availability.

## Test Plan
### Unit
- `tests/test_calendar_availability_answerer.py` — gate returns `handled=False` when disabled (and asserts no settings/intent work beyond the cheap check); non-scheduling → `handled=False`; resolved+available → Russian "available" answer; busy/out-of-rules → "not available"; `NoMatch`/`Ambiguous` → one clarifying turn then escalate; not-connected / `reconnect_needed` → escalate; provider/token failure → escalate to calendar operator (no fabricated answer).

### Integration
- Pipeline placement test: `CalendarAvailabilityAnswerer` runs before `GroundedRagAnswerer`; a calendar-disabled project falls through to RAG unchanged (regression guard).

## Automated E2E verification
- `tests/e2e/test_e2e_epic11_availability.py` — enable project + connect operator (mocked Google) → customer asks about a configured service at a free time → Russian "available"; at a busy/out-of-hours time → "not available"; ambiguous service → clarify→escalate; provider error → escalate. `@pytest.mark.e2e`, `@pytest.mark.epic("11")`.
- Regression: `tests/e2e/test_e2e_epic11_disabled_noop.py` — calendar-disabled project: an availability-shaped question behaves identically to today (RAG/HITL), no calendar latency.

## Manual Verification
1. Enable a project, connect the operator's Google calendar.
2. Ask the bot (as a customer) "можно записаться на маникюр в субботу в 15:00?" → correct Russian available/not-available answer reflecting the calendar + service rules.
3. Ask for an unconfigured service → one clarifying question, then escalation.
4. Disable the project → same question now flows to RAG/HITL with no calendar behavior.

## Done Criteria
- 100% coverage on `availability_answerer.py` + the pipeline wiring (all branches: disabled, non-intent, resolved-available, not-available, clarify, not-connected, provider failure).
- `ruff check .` passes; `pytest -m e2e` green.
- Disabled-project regression test proves zero behavior change when off.
- `epic11_signoff.sh` runs the connect→availability round-trip; `e2e-coverage.md` updated.
