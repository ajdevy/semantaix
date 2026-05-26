# Story 13.04 — `services_nl_ops` api (regex intent parser + state machine + 4 endpoints)

## Objective
Ship the api half of FR-24 Path B (Russian natural-language operator dialog): a regex-based intent parser (`parse_service_intent`) that fails closed on ambiguity, a `ServicesNlOpsRepository` state machine (mirrors `admin_nl_ops`; TTL 600s; `secrets.token_urlsafe(16)` confirm token; atomic `consume` via `hmac.compare_digest`; single pending session per `(project_id, operator)`; soft-delete with 30-day retention), and four endpoints (`POST .../services/nl-ops`, `POST .../services/nl-ops/{session_id}/confirm`, `POST .../services/nl-ops/{session_id}/cancel`, `GET .../services/nl-ops/latest-pending`). Confirm verifies session-owner before consuming the token (closes cross-operator replay). The bot dispatcher that DMs previews and routes replies lives in story 13.05. Architecture reference: lines 231, 247–248; FR reference: FR-24 Path B + acceptance criteria.

## Scope

### In Scope
- **New `services/api/app/services_nl_ops.py`**:
  - `ServicesNlOpsRepository(*, db_path)` (sync `sqlite3`; reads `services_nl_op_sessions` table created in 13.01):
    - `create_pending(*, project_id, originating_operator, op_type, payload, preview, ttl_seconds=600, now) -> NlOpSession` — returns plaintext `confirm_token = secrets.token_urlsafe(16)`; stores `hashlib.sha256(token.encode()).hexdigest()`; **atomically cancels** any existing `pending_confirmation` row for the same `(project_id, originating_operator)` (status → `cancelled`, `cancelled_at = now`, soft-delete preserves payload + emits `services_nl_op_cancelled` event with full payload) and returns the new session.
    - `consume(session_id, *, presented_token, presenter_operator, now) -> NlOpSession` — atomic single-use under `BEGIN IMMEDIATE`:
      - Verifies row exists and `status == 'pending_confirmation'`; else raises `NlOpSessionNotPending`.
      - Verifies `now < expires_at`; else flips to `expired` (soft-delete) + emits `services_nl_op_expired` with full payload + raises `NlOpSessionExpired`.
      - **Verifies `row.originating_operator == presenter_operator`**; else raises `NlOpSessionNotOwner` (cross-operator replay → 403 `not_session_owner` at the endpoint).
      - Verifies `hmac.compare_digest(sha256(presented_token), row.confirm_token_hash)`; else raises `NlOpInvalidToken`.
      - Flips status to `confirmed`, sets `consumed_at = now`, returns the row.
    - `cancel(session_id, *, presenter_operator, now)` — same ownership check; flips to `cancelled` + soft-delete + emits `services_nl_op_cancelled` with full payload.
    - `latest_pending(project_id, originating_operator, now) -> NlOpSession | None` — lazily reaps any expired row for this `(project, operator)` (flip to `expired` + emit) before returning; returns the row with status still `pending_confirmation` if one exists.
    - `purge_soft_deleted_older_than(cutoff)` — housekeeping helper (called from a scheduled job; the job itself is NOT in this story — just the method).
  - `parse_service_intent(text, *, normalizer) -> ServiceIntent` (regex-based, no LLM):
    - Ё/Е + Cyrillic-dash variants normalized via `RussianNormalizer` before matching.
    - **Start-of-message-anchored keyword trigger** `^\s*(добавь|добавьте|новая|создай|удали|измени)\s+услугу\b` (anchored here too even though the bot dispatcher also anchors — defense in depth; the api endpoint MAY be hit directly during tests).
    - **`op_type ∈ {add, edit, remove, OP_UNKNOWN}`** — `OP_UNKNOWN` is the fail-closed default; the bot translates it into the Russian `"не понял, уточните"` reply.
    - **Must-parse examples** (FR-24 acceptance):
      - `добавь услугу маникюр на 60 минут пн-сб 10-19 цена 2000 описание: классический и аппаратный` → `op_type=add, name="маникюр", duration_minutes=60, service_days=["mon".."sat"], working_hours={"mon":[["10:00","19:00"]],...}, price_text="2000", description="классический и аппаратный"`.
      - `новая услуга стрижка детская длительность 30 мин цена 1500` → `op_type=add, name="стрижка детская", duration_minutes=30, price_text="1500"`.
      - `удали услугу маникюр` → `op_type=remove, name="маникюр"`.
      - `измени услугу маникюр цена 2500` → `op_type=edit, name="маникюр", price_text="2500"`.
      - Cyrillic-dash variants `пн–сб` (en-dash) / `пн-сб` (hyphen) / `пн—сб` (em-dash) all normalize identically; `ё`/`е` normalize identically.
    - **Must-fail-closed examples** (FR-24 acceptance):
      - `добавь услугу маникюр и педикюр` → `op_type=OP_UNKNOWN, reason="multiple_services_in_one_utterance"`.
      - `добавь услугу маникюр на полтора часа` → `op_type=OP_UNKNOWN, reason="non_digit_duration"`.
  - `ServiceIntent` frozen dataclass: `op_type, name, description, price_text, tags, duration_minutes, working_hours, service_days, date_exceptions, reason`.
- **New api endpoints** (behind `internal_service_token`, body carries `originating_operator` so the api can apply ownership verification; admin-AND-registered-project-operator gate identical to story 13.02):
  - `POST /api/projects/{project_id}/services/nl-ops` — body: `{originating_operator: str, raw_text: str}`. Calls `parse_service_intent(raw_text)`; if `OP_UNKNOWN` returns `{session_id: null, op_type: "OP_UNKNOWN", reason}` and stores a `clarify` row (NOT `pending_confirmation`; for audit visibility); else creates a `pending_confirmation` session and returns `{session_id, preview, confirm_token, expires_at}`. The bot DMs preview / confirm token from this response (13.05).
  - `POST /api/projects/{project_id}/services/nl-ops/{session_id}/confirm` — body: `{presented_token, presenter_operator}`. Calls `repo.consume(...)`. On success → acquires `acquire_service_upsert_lock(project_id, payload.name)` from 13.01 → dispatches to `to_thread(ProjectServiceRepository.upsert)` (or `.delete` for `op_type=remove`) → emits `services_nl_op_confirmed` log with FULL payload (`trace_id, project_id, operator, op_type, name, description, price_text, tags, duration_minutes, working_hours_json, service_days_json, date_exceptions_json`) → returns the applied row. On `NlOpSessionNotOwner` → 403 `not_session_owner`; `NlOpSessionExpired` → 410 `session_expired`; `NlOpInvalidToken` → 401 `invalid_token`; `NlOpSessionNotPending` → 410 `session_not_pending`.
  - `POST /api/projects/{project_id}/services/nl-ops/{session_id}/cancel` — body: `{presenter_operator}`. Calls `repo.cancel(...)` (same ownership check, no token required because cancel is a no-op on state, not a state mutation that needs replay protection beyond ownership).
  - `GET /api/projects/{project_id}/services/nl-ops/latest-pending?originating_operator=...` — returns the current pending session for that pair, or `null`. Reaps expired-but-not-yet-flipped rows lazily.
- **Permission split for `op_type=remove`** — at confirm-time, the api re-applies `authorize_service_remove(actor_role, is_registered_project_operator)` from 13.02; admin → 403 `admin_cannot_remove_service` even if the propose succeeded (admin could submit a `удали услугу` proposal but cannot confirm a remove — this models "the admin's confirmation alone cannot delete a row"). Add/edit confirms apply `authorize_calendar_config` (same gate as 13.02's `POST`).
- **R1 refinement (post-13.04):** `добавь услугу <name>` (no other fields) is valid and creates a catalog-only entry; the preview is `Создать услугу «<name>».`

### Out of Scope
- The bot dispatcher (`services/bot_gateway/app/services_nl_dialog.py`) — story 13.05.
- Scheduled job to call `purge_soft_deleted_older_than` (a future epic / can land alongside Epic 13 cleanup).
- LLM-based intent extraction (explicitly future epic per FR-24).
- The `services_nl_op_sessions` table itself (created in 13.01).

## Implementation Notes
- **Mirror `admin_nl_ops.py` exactly** — same state-machine vocabulary (`pending_confirmation → confirmed | cancelled | expired`), same TTL handling, same `hmac.compare_digest`, same soft-delete posture. Read `services/api/app/admin_nl_ops.py` before writing this module; deviate only where Epic 13 demands (project-scoped key, full-payload audit logging instead of admin's keys-only).
- **Single-pending atomicity** — `create_pending` runs inside `BEGIN IMMEDIATE`; it `SELECT … FROM services_nl_op_sessions WHERE project_id=? AND originating_operator=? AND status='pending_confirmation'`, flips any hit to `cancelled` (emits the cancellation event), then inserts the new row. The transaction guarantees no race where two concurrent proposes from the same operator end with two pending rows.
- **Full-payload audit logging** — `services_nl_op_confirmed` / `_cancelled` / `_expired` log the entire payload (name, description, price_text, tags, all scheduling JSON). Operator-published service content is NOT a secret per FR-24 / decision-log H5; the FR-18 / NFR-3 redaction rule remains scoped to OAuth tokens / encryption keys. Add a log-capture test asserting `price_text` is present (defensive — catches a future cargo-cult redaction mistake).
- **Confirm path applies the lock** — same `acquire_service_upsert_lock(project_id, name)` from 13.01 wraps the `to_thread(repo.upsert)` call so slash + NL converge under one lock.
- **`OP_UNKNOWN` session row** — written with `status='clarify'` and `payload_json` carrying `{raw_text, reason}`. This makes parser-coverage gaps queryable post-hoc (helpful when deciding which regex extensions to add in a future story).
- **Idempotent retries** — confirm + cancel are idempotent against an already-final row: a second confirm on a `confirmed` row returns 410 `session_not_pending` (NOT 200; we don't want silent double-apply). A second cancel on `cancelled` returns 410 similarly.

## Test Plan

### Unit
- `tests/test_parse_service_intent.py`:
  - All FR-24 must-parse examples extract exactly the documented fields.
  - All FR-24 must-fail-closed examples return `op_type=OP_UNKNOWN` with the documented `reason`.
  - Ё/Е normalization (`добавь услугу ёжик` → `name="ежик"` via lemma normalization; OR both forms accepted depending on parser policy — pin the policy in the test).
  - All three dash variants (`пн-сб`, `пн–сб`, `пн—сб`) produce identical service-day lists.
  - Non-anchored input (mid-message) returns `OP_UNKNOWN`.
- `tests/test_services_nl_ops_repository.py`:
  - `create_pending` → row exists with `status='pending_confirmation'`, `expires_at = now + ttl`, `confirm_token_hash = sha256(token)`.
  - Second `create_pending` for same `(project, operator)` → first row's status flips to `cancelled` (with `cancelled_at`), new row created, `services_nl_op_cancelled` event emitted for the prior row with full payload.
  - `consume` happy path → status `confirmed`, `consumed_at` set, returns the row.
  - `consume` with wrong token → raises `NlOpInvalidToken`; status unchanged.
  - `consume` with wrong `presenter_operator` → raises `NlOpSessionNotOwner`; status unchanged. **(Story-level contract guarantee: confirm verifies session-owner.)**
  - `consume` after `expires_at` → raises `NlOpSessionExpired`; status flipped to `expired`; `services_nl_op_expired` event emitted with full payload.
  - `consume` of already-confirmed row → raises `NlOpSessionNotPending`.
  - `cancel` with correct owner → flips to `cancelled` + event emitted; second `cancel` → raises `NlOpSessionNotPending`.
  - `latest_pending` reaps an expired row lazily (returns `None` after the reap and after the reap emits `services_nl_op_expired`).
  - **Single-pending-per-`(project_id, operator)` invariant test** — interleave two `create_pending` calls; assert exactly one row has `status='pending_confirmation'` at any time and the prior was flipped to `cancelled` BEFORE the new row was committed (use a `threading.Event` or `unittest.mock.patch` on the SQLite cursor to detect interleaving).

### Contract
- `tests/test_api_services_nl_ops_contract.py`:
  - `POST .../services/nl-ops` with a must-parse text → 200 with `{session_id, preview, confirm_token, expires_at}`; session row exists.
  - `POST .../services/nl-ops` with `добавь услугу маникюр и педикюр` → 200 with `{session_id:null, op_type:"OP_UNKNOWN", reason:"multiple_services_in_one_utterance"}`; a `clarify` row exists.
  - `POST .../confirm` with correct token + matching `presenter_operator` → 200 + row created in `project_services` + `services_nl_op_confirmed` log with FULL payload (including `price_text`, `description`).
  - `POST .../confirm` with mismatched `presenter_operator` → 403 `not_session_owner`. **(Endpoint-level contract guarantee: confirm verifies session-owner.)**
  - `POST .../confirm` with wrong token → 401 `invalid_token`.
  - `POST .../confirm` past `expires_at` → 410 `session_expired`; `services_nl_op_expired` log emitted.
  - `POST .../confirm` second time on a confirmed session → 410 `session_not_pending`.
  - `POST .../confirm` for an `op_type=remove` proposal with admin role (registered) → 403 `admin_cannot_remove_service`.
  - `POST .../cancel` happy + wrong-owner + already-cancelled flows.
  - `GET .../latest-pending` returns the current pending row, or `null` when none / when prior row expired (lazy reap).
  - Two concurrent `POST .../services/nl-ops` calls from the same `(project, operator)` → only one ends with `status='pending_confirmation'`; the other ended as `cancelled` AND emitted its `services_nl_op_cancelled` event.

### Integration
- `tests/test_services_nl_ops_lock_integration.py` — confirm-path `to_thread(repo.upsert)` runs under `acquire_service_upsert_lock`; two concurrent confirms (one NL, one slash) for the same `(project_id, lower(name))` serialize (no overlapping execution captured via fake repo with `asyncio.Event`).

## Automated E2E verification
- `tests/e2e/test_e2e_epic13_nl_ops_api.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("13")`, `@pytest.mark.story("13-04")`): boot api against a fresh `.data/`; `POST .../services/nl-ops` with the canonical FR-24 add-payload → `POST .../confirm` with the returned token + matching operator → row visible in `project_services`; `services_nl_op_confirmed` log carries the full payload. Plus a cross-operator-replay scenario (operator B presents operator A's token → 403). Plus an expired-then-replayed scenario (advance the injected clock → 410 `session_expired`).

## Manual Verification
1. `curl -X POST .../api/projects/1/services/nl-ops -d '{"originating_operator":"opA","raw_text":"добавь услугу маникюр на 60 минут пн-сб 10-19 цена 2000"}'` → response has `session_id` + `confirm_token` + `preview`.
2. `curl -X POST .../api/projects/1/services/nl-ops/{session_id}/confirm -d '{"presented_token":"<token>","presenter_operator":"opA"}'` → row created.
3. Re-fire confirm with `presenter_operator="opB"` (different operator) → 403 `not_session_owner`.
4. Re-fire confirm on the original session → 410 `session_not_pending`.
5. Trigger a second propose for the same `(1,"opA")` while one is pending → the first session is cancelled (visible in `services_nl_op_sessions` rows + cancellation log).
6. `curl .../latest-pending?originating_operator=opA` → returns the new pending row.

## Done Criteria
- 100% coverage on `services/api/app/services_nl_ops.py` (repository + parser + exceptions) and the 4 new endpoints.
- `ruff check .` passes.
- **Single-pending-per-`(project_id, operator)`** invariant enforced + tested (concurrent-create + atomic-cancel-of-prior).
- **Confirm verifies session-owner** (presenter operator must equal originating operator) — explicit endpoint-level + repo-level tests.
- All FR-24 "must parse" + "must fail closed" regex examples covered.
- `services_nl_op_confirmed` / `_cancelled` / `_expired` events log the FULL payload (with log-capture assertion that `price_text` + `description` are present).
- Confirm of `op_type=remove` rejects admin (403) regardless of registered-operator status.
