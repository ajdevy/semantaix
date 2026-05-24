# Story 11.02 — OAuth connect flow (api: consent URL + callback + encrypted store)

## Objective
Implement the 3-legged Google OAuth Authorization-Code flow on the api side: build a read-only consent URL bound to a single-use `state`, and a browser-facing callback that validates+consumes the `state`, exchanges the code for tokens, and stores an encrypted refresh token for the `(project, operator)`. Plus a disconnect path. This is the api surface the Telegram command (11.03) drives.

## Scope

### In Scope
- `services/api/app/calendar/oauth.py` `CalendarOAuthClient(*, client_id, client_secret, redirect_uri, scopes=["https://www.googleapis.com/auth/calendar.readonly"])`:
  - `build_consent_url(*, state) -> str` — uses `google-auth-oauthlib` `Flow` (offline access, `prompt=consent` to force a refresh token).
  - `exchange_code(*, code) -> OAuthTokens` — `to_thread`-friendly sync `Flow.fetch_token`; returns `OAuthTokens(refresh_token, access_token, expiry)`. Raises `OAuthExchangeError`.
  - `revoke(*, refresh_token) -> None` — best-effort POST to Google's revocation endpoint (httpx); swallow+log failure (caller still deletes locally).
- api endpoints (`services/api/app/main.py`), reusing `internal_service_token` auth for the connect-initiation call from bot_gateway:
  - `POST /calendar/connect/initiate` (internal) — body `{project_id, operator}`. Validates the project is enabled and `operator` is the designated calendar operator (else 400). Mints `state` via `CalendarOAuthStateRepository.create`, returns `{"consent_url": ...}`.
  - `GET /calendar/oauth/callback?state=&code=` (**browser-facing**, public via nginx) — validates+consumes `state` (`InvalidOAuthState` → 400 HTML), `to_thread(client.exchange_code)`, `to_thread(token_repo.upsert)`, sets status `connected`. Renders a minimal HTML success page (Russian). Errors render an HTML failure page (no stack trace). **Rate-limited** per a simple in-memory/DB throttle.
  - `POST /calendar/disconnect` (internal) — body `{project_id, operator}`. Best-effort `client.revoke` then `token_repo.delete`. Returns `{"disconnected": true}`.
- Wire `CalendarOAuthClient` + repos as injected singletons in api startup.

### Out of Scope
- Telegram command UX (11.03) — this story is exercised via direct api calls/tests.
- Access-token refresh + revocation-on-use detection + incidents (11.04).
- freeBusy / availability / answerer (11.04–11.07).

## Implementation Notes
- **`state` is the sole identity binding** — the browser at the callback is not Telegram-authenticated. `state` must be single-use (consume on first callback) with a TTL (`settings.calendar_oauth_state_ttl_seconds`, default 300), per 11.01.
- google-auth owns the token transport (sync) — call inside `asyncio.to_thread`. Reject `google-api-python-client`.
- The callback is the one api route that returns **HTML**; keep it minimal and Russian. Never include `code`/tokens in the response or logs.
- Rate-limit the callback + initiate (unauthenticated callback triggers a token exchange — abuse surface). Per-`state` single-use already bounds replay; add a coarse per-IP/per-operator throttle.
- `prompt=consent` + `access_type=offline` so Google returns a refresh token on re-consent (overwrite via 11.01 upsert).

## Test Plan
### Unit
- `tests/test_calendar_oauth_client.py` — `build_consent_url` includes scope/state/offline/consent params; `exchange_code` maps a stubbed Flow result to `OAuthTokens` and raises `OAuthExchangeError` on failure; `revoke` swallows httpx errors. Mock the Flow + httpx per existing client test harness.

### API contract
- `tests/test_api_calendar_oauth_contract.py` — `initiate` returns a consent_url for an enabled project + designated operator; 400 when disabled or wrong operator; `callback` happy path stores an encrypted token + renders success HTML; 400 HTML on forged/expired/replayed `state`; 400 HTML on exchange failure (nothing stored); `disconnect` revokes + deletes. Assert no token/`code` in any response body or captured logs.

### Integration
- Stub Google: fake `Flow` (capture redirect, return canned tokens) + fake httpx for revoke, mirroring `tests/test_openrouter_client.py` mocking style.

## Automated E2E verification
- `tests/e2e/test_e2e_epic11_oauth_connect.py` — drives `initiate → (simulated Google redirect) → callback → token stored → disconnect`. Mocks Google token exchange; `@pytest.mark.e2e`.

## Manual Verification
1. Configure a real Google OAuth client in env; enable a project + designate an operator (via 11.01 repo).
2. `curl -X POST .../api/calendar/connect/initiate -H 'Authorization: Bearer <internal>' -d '{"project_id":1,"operator":"@op"}'` → open the `consent_url` in a browser, authorize.
3. Confirm the callback shows the Russian success page and `calendar_operator_tokens` has an encrypted row.
4. `POST /calendar/disconnect` → row removed.

## Done Criteria
- 100% coverage on `oauth.py` + the three endpoint handlers (incl. all error branches: invalid state, expired state, exchange failure).
- `ruff check .` passes.
- Tokens / `code` / encryption key never in response bodies or logs (log-capture assertion).
- Callback rate-limit covered by a test.
