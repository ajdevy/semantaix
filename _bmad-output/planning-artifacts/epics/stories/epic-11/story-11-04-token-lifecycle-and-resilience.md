# Story 11.04 — Token lifecycle, freeBusy client, and resilience

## Objective
Make calendar access robust: mint/cache access tokens, refresh under a single-flight lock, detect refresh-token expiry/revocation and recover gracefully (reconnect state + operator notice + incident + cleanup), and provide the `freeBusy` httpx client with explicit timeout and `429` handling. This is the "talk to Google with a valid token, and never guess on failure" layer.

## Scope

### In Scope
- `services/api/app/calendar/oauth.py` (extend `CalendarOAuthClient`): `refresh(*, refresh_token) -> AccessToken` — sync `google-auth` `Credentials.refresh()`, returns `(access_token, expiry)`; raises `TokenRefreshFailed` on invalid_grant / revoked / expired refresh token.
- `services/api/app/calendar/access_token_cache.py` `AccessTokenProvider(*, oauth_client, token_repo, clock, lock_factory)`:
  - `get_access_token(project_id, operator) -> str` — returns a cached token if not within the skew window of expiry; otherwise refreshes. **Single-flight**: a per-`(project,operator)` `asyncio.Lock` so concurrent inbound messages don't double-refresh / race the SQLite write.
  - On `TokenRefreshFailed`: set token status `reconnect_needed`, **delete** the dead token (clear poison row), emit an incident, and notify the operator (Telegram) to re-run `/connect_calendar`; raise `CalendarReconnectNeeded` to the caller.
- `services/api/app/calendar/calendar_client.py` `CalendarFreeBusyClient(*, http_client, clock)`:
  - `query_busy(*, access_token, calendar_id="primary", time_min, time_max) -> FreeBusy` — `POST /freeBusy` over the **injected** `httpx.AsyncClient` with explicit timeout (`settings.calendar_http_timeout_seconds`, default 10). Returns a frozen `FreeBusy` (list of busy intervals, tz-aware).
  - Failure handling: `429` → respect `Retry-After`, one bounded retry, then `CalendarProviderError`; `5xx`/timeout → one bounded retry then `CalendarProviderError`; never raise raw httpx upward.
- Incident emission for: refresh failure (token dead), repeated freeBusy provider error/timeout, `429` exhaustion — via the Epic-02 incident engine.

### Out of Scope
- The pipeline answerer + escalation routing (11.07 consumes `CalendarReconnectNeeded` / `CalendarProviderError`).
- Availability math (11.05) and service resolution (11.06).

## Implementation Notes
- **Clock and `asyncio.Lock` are injected** so the near-expiry branch and the single-flight path are deterministically testable (project-context rule).
- Sync repo + google-auth calls run via `asyncio.to_thread`; the `freeBusy` call is async httpx.
- A long-lived injected `httpx.AsyncClient` (not per-call) for pooling.
- Exceptions are the layer's contract: `TokenRefreshFailed` (repo/oauth), `CalendarReconnectNeeded` / `CalendarProviderError` (provider) — the answerer (11.07) translates these to skips/escalations. Never `except Exception` so broadly that `asyncio.CancelledError` is swallowed.
- Incidents reuse the existing incident repository/engine; include `project_id`/`operator`/`trace_id`, never the token.

## Test Plan
### Unit
- `tests/test_calendar_access_token_cache.py` — returns cached token when fresh; refreshes when within skew (frozen clock); single-flight: two concurrent `get_access_token` calls trigger exactly **one** refresh (assert via a counting fake); `TokenRefreshFailed` → status set `reconnect_needed` + token deleted + incident emitted + operator notified + raises `CalendarReconnectNeeded`.
- `tests/test_calendar_freebusy_client.py` — parses busy intervals; `429` with `Retry-After` retries once then raises `CalendarProviderError`; `5xx`/timeout retry-then-raise; success after one retry. Mock httpx with `side_effect` list per the weather-client harness.

### Integration
- Fake incident sink + fake Telegram sender capture the reconnect notification + incident on refresh failure.

## Automated E2E verification
- `tests/e2e/test_e2e_epic11_token_expiry.py` — simulate an expired/revoked refresh token on next use → assert operator moved to reconnect state, incident emitted, token cleared, and the customer path escalates (paired with 11.07). `@pytest.mark.e2e`.

## Manual Verification
1. Connect a calendar, then revoke access in the Google account.
2. Trigger an availability question → operator receives a "reconnect" DM, an incident appears in the Alerts surface, the token row is gone, and the customer is escalated (no error).

## Done Criteria
- 100% coverage on `access_token_cache.py`, the `refresh` addition, and `calendar_client.py` (all retry/error branches).
- `ruff check .` passes.
- Single-flight verified (exactly one refresh under concurrency).
- Tokens never logged; incidents carry no secret material.
