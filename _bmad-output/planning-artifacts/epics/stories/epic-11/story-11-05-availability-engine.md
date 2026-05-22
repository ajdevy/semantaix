# Story 11.05 — Availability engine (pure `compute_availability` + service rules)

## Objective
Implement the correctness core: a **pure, clock-injected, timezone-aware** function that decides whether a requested start time is available, given busy intervals and a service's rules. No I/O — exhaustively unit-tested (DST, the shipped offset, slot-fitting, working-hours windows, service-days, date exceptions). Independent of the OAuth branch.

## Scope

### In Scope
- `services/api/app/calendar/availability.py`:
  - `compute_availability(*, now, requested_start, busy, service_rule, project_tz) -> AvailabilityResult` — pure function. Returns `available` / `not_available(reason)` where reason ∈ `{busy, outside_working_hours, wrong_service_day, date_exception, in_past, outside_lookahead}`.
  - Rule: a request is **available** iff the block `[requested_start, requested_start + duration)` (a) is fully free of `busy` intervals, (b) lies entirely within a configured working-hours window for that weekday, (c) the date is a configured service-day and not a date-exception/closure, (d) is not in the past and within the look-ahead horizon. All comparisons in `project_tz`, normalized to UTC for interval math.
  - Frozen dataclasses `ServiceRule` (duration, working-hours windows per weekday, service-days, date exceptions) and `AvailabilityResult`.
- `ServiceRule` parsing from the `calendar_service_rules` JSON columns (working_hours/service_days/date_exceptions), with RU public-holiday closures resolved via the existing `holidays` library for the project's country.
- Helpers: parse `working_hours_json` as one-or-more `[start,end)` windows per weekday (supports a lunch gap); `service_days_json` as weekday set; `date_exceptions_json` as explicit closed dates.

### Out of Scope
- Fetching busy intervals (11.04 freeBusy client) — this function receives `busy` as input.
- Service-name resolution (11.06) and the answerer (11.07).
- Suggesting alternative slots (only answers the asked time; alternatives are a possible follow-up, not in v1).

## Implementation Notes
- **No `datetime.now()` inside** — `now` and `requested_start` are passed in as tz-aware datetimes (project-context rule). Use stdlib `zoneinfo`.
- DST correctness: convert wall-clock working-hours windows in `project_tz` to absolute instants per date (handle spring-forward/fall-back); compare busy/requested in UTC.
- Holidays: `holidays.country_holidays(country, years=...)`; a holiday date is treated as a closure unless explicitly overridden by a service rule. Country derived from project settings/`hitl_runtime_config` locale.
- Determinism: the function is total and side-effect-free; every branch is reachable by constructing inputs.

## Test Plan
### Unit (exhaustive — this is the correctness core)
- `tests/test_calendar_availability.py`:
  - free + within hours + service-day → `available`.
  - overlapping a busy interval (incl. partial overlap at block edges `[start,start+duration)`) → `not_available(busy)`.
  - start within hours but block runs past the window end → `not_available(outside_working_hours)`.
  - lunch-gap window: a slot inside the gap → not available; before/after → available.
  - non-service weekday → `wrong_service_day`; explicit date exception / RU holiday → `date_exception`.
  - past time → `in_past`; beyond look-ahead → `outside_lookahead`.
  - **DST**: a request on a spring-forward day and on the `Europe/Moscow` offset classified correctly with a frozen `now`.

## Automated E2E verification
- None directly (pure function); exercised end-to-end via 11.07.

## Manual Verification
- N/A (pure function). Verified via the unit matrix + the 11.07 round-trip.

## Done Criteria
- 100% branch coverage on `availability.py` (every `reason` and the available path).
- `ruff check .` passes.
- Tests are deterministic (frozen `now`, fixed `project_tz`); no real clock.
