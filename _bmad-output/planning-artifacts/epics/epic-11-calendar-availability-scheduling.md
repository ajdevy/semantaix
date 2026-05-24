# Epic 11: Calendar Availability & Scheduling (read-only)

## Goal
Let a project's designated **calendar operator** connect their own Google Calendar from Telegram (3-legged OAuth, read-only scope), and let the bot answer customer availability questions ("is service X free at date/time Y?") by intersecting that calendar's `freeBusy` with per-service scheduling rules — answering in Russian, in the project timezone. The capability is **opt-in per project and default-off**; when disabled it is a silent no-op in the answer pipeline. Booking/event creation is explicitly out of scope (read-only availability first). Uncertainty always escalates to the calendar operator rather than guessing.

## In Scope
- New SQLite store `.data/semantaix_calendar.db` (idempotent schema, default-off bootstrap): `calendar_project_settings` (enablement, designated calendar operator, project timezone, freeBusy look-ahead), `calendar_operator_tokens` (Fernet-encrypted refresh tokens, upsert-keyed by project+operator), `calendar_oauth_pending_state` (single-use `state` with TTL), `calendar_service_rules` (duration, working-hours windows, service-days, date exceptions).
- New `services/api/app/calendar/` package: `CalendarSettingsRepository`, `CalendarTokenRepository`, `CalendarOAuthStateRepository`, `CalendarOAuthClient` (`google-auth-oauthlib` Flow + `google-auth` refresh), `CalendarFreeBusyClient` (httpx), pure `compute_availability(...)`, `service_resolver`, `CalendarAvailabilityAnswerer`.
- 3-legged OAuth connect: api endpoints to build a read-only consent URL and a browser-facing callback that validates+consumes a single-use `state`, exchanges the code, and stores an encrypted refresh token; disconnect (best-effort Google revoke + local delete). Callback is rate-limited.
- Telegram `/connect_calendar` and `/disconnect_calendar` operator commands (gated to authorized operators), mirroring the existing operator-command dispatch.
- Token lifecycle + resilience: access-token mint/cache, single-flight refresh (`asyncio.Lock`), refresh-token expiry/revocation detection → reconnect state + Telegram notice + incident emission + dead-token cleanup; explicit timeouts; Google `429` `Retry-After` bounded retry.
- `CalendarAvailabilityAnswerer` placed **before** `GroundedRagAnswerer` in `AnswerPipeline`: opt-in tri-state gate (not-enabled → silent no-op; enabled-but-disconnected → "not connected" / escalate; connected → answer), Russian service resolution (FR-22), availability computation in project tz, escalate-on-uncertainty routed to the calendar operator.
- Per-project, per-service config (the `hitl_runtime_config` config-in-DB pattern), editable without redeploy; RU public holidays honored via the existing `holidays` library.
- **Enable / disable / service-config surface:** **enable is implicit in `/connect_calendar`** — a successful OAuth callback flips the project to enabled and records the connecting operator atomically with the token upsert (no separate `/calendar_on` command or `/enable` endpoint). **Disable is explicit and shared:** both the designated **calendar operator** and an **admin** can `/calendar_off` (Telegram) or call the internal disable endpoint; disable keeps the stored token. Re-enable after disable = the operator re-runs `/connect_calendar`. **Service-rule config is shared** (operator + admin via Telegram + internal endpoints). **Only the operator may disconnect/delete** the integration — an admin can pause it but cannot enable it and cannot delete the operator's connected calendar.

## Out of Scope
- **Booking / event creation / modification** (read-only availability only).
- **Multi-operator selection** within a project (v1 = exactly one designated calendar operator).
- **Multi-calendar selection** (v1 = the operator's primary calendar only).
- **freeBusy result caching** (v1 makes one live call per question to avoid stale "free").
- Vector/embedding work, RAG changes, or any change to the existing four-layer grounding pipeline beyond inserting the calendar answerer ahead of it.
- A **web** admin UI for calendar settings (enable/disable + service config ship as Telegram commands + internal api endpoints in story 11.08; a web `/admin/*` surface is deferred).
- Natural-language calendar configuration (the epic-10-style NL dialog for calendar setup is a possible follow-up, not in v1).

## Dependencies
- **Epic 01 / refactor** — `AnswerPipeline` + `Answerer` Protocol; `scheduling_context` intent regex + `RussianNormalizer` (reused for intent + FR-22 service resolution).
- **Epic 02** — incident engine (OAuth/refresh/freeBusy failures emit incidents per the carry-forward rule).
- **Epic 08 / 10** — project + multi-operator scoping (`projects`, `operators`, `project_id` resolution on the open HITL ticket); calendar settings are project-scoped.
- **Epic 09** — operator-command dispatch + gating pattern in `bot_gateway` (`/connect_calendar` follows it); `TelegramBotSender` for DMs.
- **External** — Google Cloud OAuth client (client id/secret, redirect URI) provisioned; `calendar.readonly` scope; OAuth app verification is a release-readiness dependency (PRD §9).

## Exit Criteria
- After a clean `docker compose up`, `semantaix_calendar.db` exists with all four tables and **calendar is disabled for every project** (default-off); a customer availability question on a disabled project behaves exactly as today (no calendar behavior, no added latency, falls through to RAG/HITL).
- An operator runs `/connect_calendar`, receives a consent link, authorizes on Google, and the callback stores an encrypted refresh token; the operator gets a Telegram confirmation. Re-running overwrites the token; `/disconnect_calendar` removes it.
- With the project enabled + operator connected, a customer asking about a configured service at a specific time gets a correct Russian **available / not-available** answer that respects calendar busy blocks **and** the service's duration / working hours / service-days / holiday exceptions, evaluated in the project timezone.
- A no-match / ambiguous / no-service-named question yields exactly one clarifying turn, then escalates; a provider/token failure escalates to the calendar operator (never a fabricated answer, never a 500).
- A Google-side revocation or expired refresh token is detected on next use → operator moved to reconnect state + notified + an incident is emitted + the dead token cleared.
- `ruff check .` clean; `pytest --cov` shows **100%** line coverage on the new `services/api/app/calendar/` modules and new endpoint/command handlers; tokens, secrets, and the encryption key never appear in logs or answer-trace metadata (verified by a log-capture test).

## Automated E2E verification
- Story-aligned tests under `tests/e2e/test_e2e_epic11_*.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("11")`).
- New scripted signoff: `scripts/epic11_signoff.sh`.
- Matrix updated in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
