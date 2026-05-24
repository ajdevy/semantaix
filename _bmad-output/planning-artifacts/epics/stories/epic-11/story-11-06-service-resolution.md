# Story 11.06 — Service resolution from Russian text (FR-22)

## Objective
Map a customer's free Russian message to a configured service via lemma matching, returning `resolved` / `none` / `ambiguous` so the answerer (11.07) can clarify-once-then-escalate. Reuses `RussianNormalizer`; never guesses a service. Independent of the OAuth branch.

## Scope

### In Scope
- `services/api/app/calendar/service_resolver.py`:
  - `resolve_service(*, text, service_rules, normalizer) -> ServiceMatch` where `ServiceMatch` is one of `Resolved(service)`, `NoMatch`, `Ambiguous(candidates)`.
  - Lemma-match: lemmatize the customer text and each `service_rule.name` via `RussianNormalizer.lemmas`; a service matches if its name-lemmas are a subset of (or sufficiently overlap) the message lemmas. Exactly one match → `Resolved`; zero → `NoMatch`; ≥2 → `Ambiguous`.
  - Also expose `extract_requested_start(text, *, now, project_tz) -> datetime | None` OR clearly defer time parsing to the answerer — **decision: a minimal Russian date/time extractor lives here** (e.g. "в субботу в 15:00", "завтра в 3 часа") returning a tz-aware datetime or `None`; ambiguous/locale edge cases return `None` and the answerer asks/escalates.
- Russian clarifying-copy constants (illustrative; actual copy configurable as data): no-match prompt, ambiguity prompt, no-service-named prompt.

### Out of Scope
- The clarify/escalate orchestration + one-turn state (11.07 owns the turn-taking and HITL routing).
- Availability math (11.05) and freeBusy (11.04).

## Implementation Notes
- Reuse `RussianNormalizer` (razdel + slang + pymorphy3) — do **not** add a parallel tokenizer/intent detector (project-context rule). The existing `scheduling_context` intent regex gates whether we even attempt resolution (handled in 11.07).
- Matching must be robust to inflection ("маникюр" / "на маникюр" / "маникюра") — that's exactly what lemma matching buys.
- Time extraction is intentionally conservative: when in doubt return `None` so the answerer clarifies rather than guessing a time (feeds the escalate-on-uncertainty contract).
- Pure functions, no I/O; `now`/`project_tz` injected for deterministic tests.

## Test Plan
### Unit
- `tests/test_calendar_service_resolver.py`:
  - exact lemma match (incl. inflected forms) → `Resolved`.
  - unknown service → `NoMatch`; two overlapping configured services → `Ambiguous(candidates)`.
  - message with a time but no service → `NoMatch` (no service named).
  - `extract_requested_start`: parses "завтра в 15:00" / "в субботу в 3 часа" against a frozen `now`+tz; returns `None` on ambiguous/unparseable input.

## Automated E2E verification
- Exercised via 11.07 (the resolve→clarify→escalate path) end-to-end.

## Manual Verification
- N/A (pure functions); verified via unit matrix + 11.07.

## Done Criteria
- 100% coverage on `service_resolver.py` (Resolved / NoMatch / Ambiguous + time-parse success/None branches).
- `ruff check .` passes.
- Deterministic tests (frozen `now`, fixed tz); reuses `RussianNormalizer` (no parallel tokenizer).
