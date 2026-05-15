# Story 10.02 — Admin login-code auth (api + Telegram DM)

## Objective
Implement the admin login flow end to end on the api side: request a one-time 6-digit code, DM it to the admin via the existing `TelegramBotSender`, verify the code, and mint an opaque 24-hour session token. All admin-mutating routes added in later stories will sit behind a `require_admin_session` dependency that reads `X-Admin-Session: <token>` and validates against the `admin_sessions` table from 10.01.

## Scope

### In Scope
- Flesh out `services/api/app/admin_auth.py` (`AdminAuthRepository`):
  - `request_code(admin_username) -> str` — returns the **plaintext** 6-digit code (caller is responsible for DM'ing it; the repo only stores the sha256). Uses `secrets.choice("0123456789")` × 6. Persists `code_sha256`, `expires_at = now + settings.admin_login_code_ttl_seconds`, `created_at = now`. Auto-invalidates any earlier non-consumed codes for the same `admin_username` by setting their `consumed_at = now` (single-active-code policy).
  - `consume_code(admin_username, code) -> AdminSession` — sha256s the code, looks up an unconsumed row with `expires_at > now`, marks it consumed, generates `secrets.token_urlsafe(32)`, sha256s it, inserts into `admin_sessions` with `expires_at = now + settings.admin_session_ttl_seconds`. Returns `AdminSession(token, admin_username, expires_at)`. Raises `InvalidLoginCode` on miss / expired / replay.
  - `validate_session(token) -> AdminSession | None` — sha256, look up by token_sha256, check `expires_at > now`. Returns `None` if invalid or expired.
  - `revoke_session(token) -> None`.
  - `purge_expired() -> int` — best-effort cleanup hook (used by tests and an api admin command if needed).
- Comparisons via `hmac.compare_digest` after hashing.
- New api endpoints (in `services/api/app/main.py`):
  - `POST /admin/login/request` — body `AdminLoginRequestModel(admin_username)`. Validates `admin_username == settings.admin_telegram_username` (403 otherwise). Resolves `chat_id` from `OperatorRepository.find_by_username(admin_username).chat_id` (400 if admin operator is missing chat_id). Calls `repo.request_code(...)`, DMs `"Ваш код входа: 123456 (5 минут)"` via `telegram_bot_sender.send_message`, returns `{"requested": true}`.
  - `POST /admin/login/verify` — body `AdminLoginVerifyRequest(admin_username, code)`. Calls `consume_code`. Returns `{"session_token": ..., "expires_at": ...}`. 401 on `InvalidLoginCode`.
  - `POST /admin/logout` — header `X-Admin-Session`. Calls `revoke_session`. Returns `{"ok": true}`.
  - `GET /admin/session/check` — header `X-Admin-Session`. Returns `{"valid": true, "admin_username": ..., "expires_at": ...}` or 401.
- Reusable dependency `require_admin_session(x_admin_session: str = Header(...)) -> AdminSession` for later stories.
- DM message text is Russian, keeps token only in the body (never logged).

### Out of Scope
- Web UI login pages (10.03).
- Project / operator / file endpoints (introduced in 10.03 and 10.04).
- NL dialog (10.05).
- RAG scoping (10.06).
- Rate limiting beyond the single-active-code-per-username invalidation. (A simple per-username throttle can be added if needed in a follow-up.)

## Implementation Notes
- Hashing: `hashlib.sha256(code.encode("utf-8")).hexdigest()`.
- Token generation: `secrets.token_urlsafe(32)` (~43 chars).
- Timestamps: ISO-8601 UTC string, same convention as other repos (`datetime.now(timezone.utc).isoformat()`).
- `TelegramBotSender` is already wired in api/main.py; reuse the singleton.
- 401 vs 403: 401 for invalid/expired/replay code or session; 403 for `admin_username` mismatch (i.e., not the configured admin).
- Logging: never log the plaintext code or the session token. Log `admin_username` and `code_id` only.

## Test Plan

### Unit
- `tests/test_admin_auth_repository.py` — generate code is 6 digits, sha256-stored, expires_at honored, second code invalidates prior, `consume_code` rejects expired, replay, wrong code, returns active session; `validate_session` rejects expired/unknown, `revoke_session` invalidates, `purge_expired` deletes only expired rows.

### API contract
- `tests/test_api_admin_auth_contract.py` — `POST /admin/login/request` happy path DMs via fake sender, returns 200; 403 when `admin_username` != settings; 400 when admin operator has no chat_id; `POST /admin/login/verify` round-trip returns a session token; 401 on wrong code, expired code, replay; `GET /admin/session/check` 200/401; `POST /admin/logout` revokes; subsequent `GET /admin/session/check` returns 401.

### Integration
- Reuse fake `TelegramBotSender` (capture-only) per `tests/test_api_*_contract.py` conventions.

## Automated E2E verification
- `tests/e2e/test_e2e_epic10_admin_login.py` — drives `request → DM intercept → verify → session check → logout → re-check fails`. Marked `@pytest.mark.e2e`.

## Manual Verification
1. `docker compose up --build api bot_gateway`.
2. `curl -X POST http://localhost/api/admin/login/request -d '{"admin_username":"@ajdevy"}' -H 'Content-Type: application/json'` — receive `{"requested": true}` and the code arrives on Telegram.
3. `curl -X POST http://localhost/api/admin/login/verify -d '{"admin_username":"@ajdevy","code":"123456"}' -H 'Content-Type: application/json'` — receive `{"session_token": "..."}`.
4. `curl http://localhost/api/admin/session/check -H "X-Admin-Session: <token>"` — 200.
5. `curl -X POST http://localhost/api/admin/logout -H "X-Admin-Session: <token>"` — 200, then re-check returns 401.

## Done Criteria
- All unit + contract + e2e tests pass.
- 100% coverage on `services/api/app/admin_auth.py` and new endpoint handlers.
- `ruff check .` passes.
- Plaintext code never appears in logs (verified by a log-capture test).
