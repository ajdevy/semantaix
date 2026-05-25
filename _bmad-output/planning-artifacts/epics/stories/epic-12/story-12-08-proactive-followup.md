# Story 12.08 — Proactive +1 day follow-up

## Objective
When a customer goes silent after the last bot message, fire exactly **one** nudge at T+1 day. Skip the nudge if the customer's target start datetime has already passed (`intent.dates` is in the past). Defer to next morning during quiet hours (project tz 21:00–10:00). Cancel the nudge if the customer replies before it fires. This story promotes `services/scheduler` from a heartbeat-only placeholder to a real job runner — its first real job is `proactive_followup`.

## Scope

### In Scope
- **Scheduler upgrade.** Replace the heartbeat loop in `services/scheduler/app/main.py` with a tickless polling runner that wakes every 60 seconds, calls registered jobs, and logs `scheduler_tick_completed` with per-job timings.
- **New job module** `services/scheduler/app/jobs/proactive_followup.py` `ProactiveFollowupJob(*, api_client, clock, project_tz_lookup)`:
  - `async def run(self) -> JobResult` called by the scheduler each tick.
  - Calls `GET /sales/followups/due?now=<iso>` → list of due rows (the api owns the actual repo query via `FollowupQueueRepository.due`).
  - For each due row:
    1. **Skip-if-stale:** load the chat's `ConversationState`; if `intent.dates` parses to a date strictly before `now.date()` (project tz), call `POST /sales/followups/{id}/skip-stale` and continue. The stale check uses the same `parse_russian_date_span(...)` from 12.07.
    2. **Quiet hours:** if `now` in project tz is between 21:00 (inclusive) and 10:00 (exclusive), re-schedule the row to today's 10:00 (project tz) via `POST /sales/followups/{id}/reschedule` and continue.
    3. **Fire:** call `POST /sales/followups/{id}/fire` (api does the actual send via `TelegramBotSender` — see below).
- **New api endpoints** (internal, service-token-gated):
  - `GET /sales/followups/due?now=<iso>` → `{rows: [FollowupRow]}` (caps at 100 per call).
  - `POST /sales/followups/{id}/skip-stale` → `{ok: true}`.
  - `POST /sales/followups/{id}/reschedule` `{new_fire_at}` → `{ok: true}`.
  - `POST /sales/followups/{id}/fire` → `{ok: true, sent: bool, fallback_text_used: bool}`.
- **Fire-handler** in api:
  - Renders the nudge via `system_prompts/nikolay_followup.txt` (Russian, one short sentence, persona-aware, includes the customer's first name if available from the Telegram chat metadata). Example: `"Данил, остались вопросы по туру 1 мая?"`. The LLM call is allowed to vary the wording; the prompt forbids new dates/prices.
  - Dispatches via `TelegramBotSender.send_message` (no media — a follow-up is text-only in v1).
  - On Telegram error: log + `mark_sent` is NOT called; instead `mark_skipped_stale(reason="telegram_send_failed")` (the queue is single-shot — no retry).
  - On success: `mark_sent` + record `state_repo.mark_bot_msg(...)`.
- **Enqueue seam in `SalesPersonaAnswerer`.** Every successful `handled=True` answerer turn calls `followup_repo.enqueue(chat_id, project_id, fire_at=now + 24h, now=now)` — replacing any prior `scheduled` row for the same chat. This is what guarantees "one nudge per silent customer at T+1d from the last bot turn."
- **Cancel-on-reply seam in the inbound pipeline.** When the customer sends a new message (handled at `/conversations/inbound`), call `followup_repo.mark_cancelled_replied(chat_id)` **before** running the pipeline (so a reply that arrives at the same instant the queue fires doesn't double-notify). This is a `services/api/app/sales/followup_cancel_hook.py` one-liner imported by the inbound route.
- **Project timezone lookup.** `project_tz_lookup(project_id) -> str` reuses the existing `hitl_runtime_config` setting (`timezone`, default `Europe/Moscow`). Same source as Epic 11.

### Out of Scope
- A second nudge at T+3d / T+7d / arbitrary cadence — v1 is exactly one nudge.
- Sentiment-driven cadence ("the customer sounded interested, nudge sooner") — out per epic.
- Customer opt-out from nudges — v1 has no opt-out command; the operator can `/sales_state` + manually mark `dormant` if needed (a follow-up will add a command for this).
- Sending media in a nudge — text only.
- Operator-side notification when a nudge fires — the operator sees nothing; the bot just nudges. Operators get a HITL ticket only on the standard escalation paths.
- Cron / APScheduler — v1 polls every 60s in a tickless loop (matches the simplicity of the current heartbeat).

## Implementation Notes
- **Scheduler is single-process.** `services/scheduler` runs as its own container; only one replica. No distributed locking needed in v1. If a future deployment requires HA, the polling loop can become a `PRAGMA busy_timeout` + `LIMIT 1 FOR UPDATE`-style queue claim — out of scope for now.
- **`fire_at` is stored UTC.** All comparisons in the repo + job are UTC; project-tz conversion happens only at the quiet-hours decision and at the nudge-rendering step (where the prompt may include a local date for context). Mirror Epic 11's tz discipline.
- **Quiet-hours reschedule is bounded.** A row can only be rescheduled within a window (max one daily push); a row rescheduled into next morning that finds the customer's date already passed at fire time still skips via `skip-stale`. There's no infinite-reschedule loop because tomorrow's quiet hours don't apply to a 10:00 fire.
- **Cancel-on-reply happens BEFORE the pipeline runs.** The inbound route imports `followup_cancel_hook.maybe_cancel(chat_id)` (a one-liner that calls `mark_cancelled_replied`) as the first thing after rate-limiting. Ordering matters: if the answerer turn re-enqueues (which it does on `handled=True`), the cancel happens first, then a fresh queue row is enqueued from the new bot turn — no leak.
- **`mark_sent` vs `mark_skipped_stale`.** The job marks `sent` only on Telegram success. Telegram failure → `skipped_stale` with a `reason` column added in this story to the `sales_followup_queue` table (`reason TEXT NULL`). The reason values are `"past_intent_date"`, `"telegram_send_failed"`, or NULL (success).
- **`/sales_state` extension.** Story 12.02 ships `/sales_state`; this story extends the output to include the chat's pending follow-up (`next_followup=2026-05-25T07:00:00Z` or `none`) by reading `FollowupQueueRepository.list_for_chat`.
- **No new external libraries.** Stdlib `asyncio.sleep` + `httpx` (already a dependency) are the only deps in the scheduler.

## Test Plan
### Unit
- `tests/test_sales_followup_queue_repository_extended.py` — adds tests for the new `reason` column (added in this story); `mark_skipped_stale(reason=...)` persists; `due` excludes rows whose `fire_at` is in the future.
- `tests/test_proactive_followup_job_fire.py` — due row + non-stale + outside quiet hours → calls `fire`; on api `{ok: true, sent: true}`, no other action.
- `tests/test_proactive_followup_job_skip_stale.py` — due row + `intent.dates` parses to a past date → calls `skip-stale`; no `fire` call.
- `tests/test_proactive_followup_job_quiet_hours.py` — due row at 22:00 project tz → calls `reschedule` to today's 10:00 project tz; no `fire` call; due row at 09:00 → same (rescheduled to 10:00); due row at 10:01 → fires.
- `tests/test_api_followups_endpoints.py` — `due` with cap, `skip-stale` round-trip, `reschedule` updates `fire_at` + leaves status `scheduled`, `fire` happy path + Telegram failure path.
- `tests/test_sales_persona_answerer_enqueues_followup.py` — every `handled=True` turn calls `followup_repo.enqueue(... fire_at=now+24h)`; a second `handled=True` turn replaces the prior row (asserted by reading the repo).
- `tests/test_sales_followup_cancel_hook.py` — `maybe_cancel(chat_id)` cancels a `scheduled` row; does NOT touch a `sent` / `skipped_stale` row.
- `tests/test_inbound_pipeline_cancels_followup_first.py` — inbound message arrives → `mark_cancelled_replied` runs before the pipeline (verified by ordering assertion on a captured call list).

### Integration
- `tests/test_proactive_followup_end_to_end.py` — enqueue a row 24h in the past, run one job tick, assert Telegram sender was called once and the row is `sent`; enqueue a stale row, tick, assert `skipped_stale(reason="past_intent_date")` and no Telegram call.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_followup.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-08")`):
  - **Fires-in-window:** customer message → answerer turn → frozen clock to T+25h (project tz 11:00) → scheduler tick → one Telegram nudge sent.
  - **Cancel-on-reply:** same setup but the customer replies at T+12h → tick at T+25h finds no due row.
  - **Skip-if-stale:** seed `intent.dates="20 апреля"` (past) → tick → `skipped_stale`, no Telegram call.
  - **Quiet-hours:** tick at project tz 22:00 → rescheduled to next 10:00; tick at 10:01 → fires.

## Manual Verification
1. Walk through scoping with a customer chat, then stop replying. Wait until T+24h (or fast-forward the clock in dev) — expect one Telegram nudge in Russian, persona-aware.
2. Reply right after the bot's last message → wait T+24h → expect no nudge.
3. Seed `intent.dates` to yesterday → wait T+24h → expect no nudge (queue marked `skipped_stale`).
4. Run `/sales_state @customer` → expect the chat's `next_followup` field to reflect the next scheduled fire time or `none`.

## Done Criteria
- 100% coverage on `proactive_followup.py`, the new api endpoints, the cancel-on-reply hook, and the new followup-enqueue branch in `sales_persona_answerer.py`.
- `ruff check .` passes; E2E green.
- Exactly one nudge per silent customer at T+1d; cancel-on-reply works; skip-if-stale works; quiet hours respected.
- `services/scheduler` now runs the real `ProactiveFollowupJob` and logs per-tick timings; the old heartbeat-only loop is gone.
- No new external libraries; stdlib + httpx only.
- All `fire_at` values stored UTC; project-tz conversion only at the decision boundary.
