---
project_name: 'Semantaix'
user_name: 'Aj'
date: '2026-05-22'
sections_completed: ['technology_stack', 'language_specific', 'framework_specific', 'testing', 'code_quality', 'workflow', 'critical_dont_miss']
existing_patterns_found: 12
status: 'complete'
rule_count: 67
optimized_for_llm: true
last_updated: '2026-05-22'
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Python 3.11** (ruff `target-version = py311`). PEP 604 unions (`str | None`), `from __future__ import annotations` atop every module.
- **FastAPI 0.115.12** + Uvicorn 0.34.2; **Pydantic Settings 2.9.1** (single `Settings` in `platform_common/settings.py`).
- **httpx 0.28.1 is THE outbound transport idiom.** External calls use httpx + frozen `@dataclass` results (mirror `weather_client.py` / `openrouter_client.py`).
- **Vendor-SDK rule — "hand-roll the request, never the cryptography."** Reject kitchen-sink clients (`google-api-python-client` etc.). A *focused* auth/crypto primitive is allowed. Make the actual API call (e.g. Calendar `freeBusy`) with httpx.
- **OAuth lives in google-auth, transport stays httpx.** The consent / token-exchange / refresh dance uses `google-auth` + `google-auth-oauthlib` (focused crypto primitive — "never hand-roll the cryptography"). The `freeBusy` query itself is a plain httpx POST. Reject `google-api-python-client`.
- **Datastore is SQLite**, one file per concern in `.data/` (WAL where cross-process reads needed). PRD/architecture say Postgres/Qdrant — the running code is SQLite; trust the code.
- **OAuth client secrets in env, per-operator tokens encrypted in SQLite.** The Google OAuth `client_id`/`client_secret`/redirect-URI live in env via `Settings` (like `openrouter_api_key`, `telegram_bot_token`) — never committed. Per-operator refresh tokens are obtained via 3-legged Authorization-Code consent (operator connects their own calendar from Telegram) and stored **encrypted at rest** in a dedicated one-file-per-concern SQLite repo, keyed by operator — never in env, never logged. Use `cryptography` (Fernet/AES) with the key in env.
- **Time is injected, never ambient.** Never call `datetime.now()` inside client/service logic — accept `now`/a clock at the seam (cf. `openrouter_client` taking `today_iso`). Enables the 100% gate on time-edge branches.
- **Timezone discipline.** Time math goes through the config-driven timezone (`hitl_runtime_config` country/timezone/location) via stdlib `zoneinfo`. All external datetimes tz-aware; store UTC ISO-8601, convert at the edge. No new date library; no naive datetime crosses a boundary.
- **Outbound failure = escalate, never guess.** 401-expired → refresh-then-retry once; 429 → respect `Retry-After`; 5xx → one bounded retry; anything unresolved → HITL, never a possibly-wrong answer. Inject httpx client + clock so retry/near-expiry branches are test-reachable.
- Russian NLP: `pymorphy3 2.0.6`, `razdel 0.5.0`, `holidays 0.96` — wrapped by `RussianNormalizer`; reuse it, don't re-tokenize.
- Tooling: `ruff 0.11.8` (line-length **100**, `select = ["E","F","I"]`, excludes `.agents`/`_bmad`), `pytest 8.3.5` + `pytest-asyncio 0.24.0` (function-scoped loop), `pytest-cov 5.0.0`, **`fail_under = 100`** on `platform_common/` + `services/`.

## Critical Implementation Rules

### Language-Specific Rules

- `from __future__ import annotations` atop every module. It makes annotations strings — if a runtime resolves them (some Pydantic/dataclass introspection), account for it (`model_rebuild()` if needed).
- **Interfaces are `typing.Protocol`, deps constructor-injected** (so they fake cleanly in tests — load-bearing for the pipeline + repos):
  ```python
  class Answerer(Protocol):
      async def try_answer(self, *, question: str, ctx: AnswerContext) -> AnswerResult: ...
  # inject collaborators via __init__(self, *, dep: SomeProtocol); never instantiate deps inside a class
  ```
- **Public methods are keyword-only**: `def f(self, *, x, y)`; repos `def __init__(self, *, db_path: str)`.
- **Value objects are immutable** — `@dataclass(frozen=True)`, construct anew. Collection fields use `field(default_factory=list)` / `tuple[str, ...]` (frozen blocks reassignment, NOT in-place mutation of a shared default).
- **Failure convention is per-layer — never blur them:**
  - *Answerers DISPATCH, they don't error.* Return `handled=False` via `self._skip(reason=...)` ONLY for "not my intent." Never raise into the pipeline:
    ```python
    try:
        ...
    except (httpx.HTTPStatusError, httpx.RequestError):
        return self._skip(reason="calendar_unavailable")  # never propagate into the pipeline
    ```
    For "my intent but degraded" (calendar owns the question, API failed) → escalate to HITL / safe handled result, do NOT silently fall through.
  - *Repositories raise typed domain errors* (`TokenNotFound`, `TokenRefreshFailed`); the answerer translates them to skips/escalation at the boundary.
  - *HTTP endpoints raise `HTTPException`* with explicit status (400 bad `state`, 302 success, 500 exchange failure). Translate errors at layer boundaries, never pass through unchanged.
  - Never `except Exception` so broadly it swallows `asyncio.CancelledError` — re-raise it.
- **Network I/O is async; SQLite is sync.** httpx is async; `*Repository` uses blocking `sqlite3`. Call sync repos from async via `await asyncio.to_thread(repo.method, ...)` — never wrap `sqlite3` in `async def`. No raw SQL outside `*Repository`.
- **Outbound HTTP: explicit timeout; long-lived injected client when stateful.** One-shot: `async with httpx.AsyncClient(timeout=10.0) as client:`. The Calendar/token client is constructor-injected and reused (pooling + token state) — not created per call.
- **Concurrent token refresh is single-flight** — guard with an `asyncio.Lock` so simultaneous inbound messages don't double-refresh / race the SQLite write.
- **`datetime` is always tz-aware** — `datetime.now(tz=...)`, never naive `utcnow()`; UTC ISO-8601 at rest.
- **Structured logging** — event name is a `snake_case verb_noun` literal; always include `trace_id` (from `ctx.trace_id`, threaded through repo dispatch too); never f-string the message:
  ```python
  logger.info("calendar_freebusy_checked", extra={"trace_id": ctx.trace_id, "busy_blocks": n})
  ```
- **Secrets/key material come from `Settings`** (env) and never land in a repo's stored columns or a log line.

### Framework-Specific Rules

- **Apps are built via `platform_common/app_factory.py`** (provides `/health/live`, `/health/ready`, `/health/startup`). New services/routers extend it — don't hand-roll an app or health checks.
- **Endpoints are thin; logic lives in modules/repositories.** Request/response bodies are Pydantic models. Service-to-service calls authenticate with `internal_service_token` (Bearer) — see `ApiClient`.
- **The answer pipeline is an ordered `AnswerPipeline([...])`** assembled in `services/api/app/main.py` (~L202). Each answerer implements the `Answerer` Protocol; **order IS the routing logic** — first `handled=True` wins, else fall through to HITL. NB: datetime/holiday/weather were *removed* as standalone stages and folded into `scheduling_context.py` as LLM signals. Decide consciously: is calendar availability (a) a new standalone answerer before `GroundedRagAnswerer`, or (b) another `scheduling_context` signal? Don't blindly copy the old weather-stage shape.
- **Reuse the existing intent seam.** Scheduling/booking intent is already detected in `scheduling_context.py` via `_RU_INTENT`/`_EN_INTENT` regex on normalized text (`запис|брон|расписани|...`). Extend that regex / the `RussianNormalizer` path — do NOT add a parallel intent detector.
- **Telegram operator commands follow the existing dispatch pattern** in `services/bot_gateway/app/` — regex-match the slash command (cf. `_SLASH_RE` in `kb_intent.py`, `_handle_admin_hitl_command`, `handle_admin_project_command`) and **gate on operator/admin username** before acting. `/connect_calendar` follows this; unauthorized senders are ignored with a logged reason.
- **The OAuth callback is a new browser-facing route** (Google's redirect target). It MUST validate the `state` param (CSRF), follow `app_factory` conventions, and raise `HTTPException` on bad/expired `state`. Decide its home (api vs web_ui) in the architecture step.
- **Tunable runtime values live in `hitl_runtime_config`** (SQLite), not hard-coded (operator routing, ack text, country/timezone/location, grounding threshold). Per-operator calendar settings (working hours, service rules) follow this config-in-DB pattern.
- **New config goes in the single `Settings`** class (`platform_common/settings.py`); read it there — don't scatter `os.getenv`.

### Testing Rules

- **100% coverage is a hard CI gate** (`fail_under = 100` on `platform_common/` + `services/`). Every new branch needs a test — each `_skip`/`return None`/error branch included. Don't reach for `# pragma: no cover` unless genuinely unreachable.
- **Test files mirror source under `tests/`** (`answerers/weather_client.py` → `tests/test_answerers_weather_client.py`). Async tests use `pytest-asyncio` (function-scoped loop).
- **Mock httpx, never hit the network.** Mirror the existing harness: a `Mock()` response with `.json.return_value` + `.raise_for_status = Mock()`, an `AsyncMock()` client in an `AsyncMock` context manager, `monkeypatch.setattr("httpx.AsyncClient", lambda timeout: cm)`. For **multi-call** flows (token exchange → freebusy) use `client.post = AsyncMock(side_effect=[resp1, resp2, ...])` (cf. `tests/test_answerers_weather_client.py`).
- **Drive error branches explicitly.** 401/refresh: `side_effect=[resp_401, refresh_resp, freebusy_resp]` where the 401's `raise_for_status = Mock(side_effect=httpx.HTTPStatusError(...))`. Also test refresh-then-still-401 (permanent failure) — required for 100%.
- **Inject time + the http client; freeze the clock.** Pass an aware `now` and the `AsyncClient` in; assert deterministic free/busy across DST and the shipped offset (`ZoneInfo("Europe/Moscow")`). Never rely on real `datetime.now()` in tests.
- **Repositories test against a tmp `db_path`** (`tmp_path/x.sqlite3`), never the real `.data/` files. Encrypted-token repo: round-trip encrypt/decrypt + `TokenNotFound`/`TokenRefreshFailed` paths.
- **E2E tests use FastAPI `TestClient`** and carry markers: `@pytest.mark.e2e`, `@pytest.mark.epic("11")`, `@pytest.mark.story("11-0X")`. CI runs `pytest` (coverage) + `pytest -m e2e`. Story-aligned ids live in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
- **Stub the LLM + Telegram sender** on the module-level singletons via `monkeypatch.setattr(..., AsyncMock(return_value=...))` for inbound/pipeline E2E (cf. existing epic E2E tests).

### Code Quality & Style Rules

- **ruff is the sole lint/format authority** — `ruff check .` must pass. Line length **100**; rule sets `E` (pycodestyle), `F` (pyflakes), `I` (isort). Imports are ruff-`I` ordered (stdlib / third-party / first-party).
- **Naming:** modules `snake_case.py`; classes `PascalCase`; repository classes end in `Repository`; answerers end in `Answerer` and expose a `name` attr; test files `test_<mirrored_path>.py`; log events `snake_case verb_noun`.
- **Russian-first content is DATA, not code.** Tunable phrase lists (slang, profanity, hedges, policy, intent) live in `data/*.txt` / `*.json` and load at runtime — add entries there, never as Python literals. Customer-facing strings are Russian; keep RU + EN parallel where the codebase already does (e.g. weather-code maps). Per-project overrides come from the `project_prompts` table.
- **Docstrings state the contract / WHY, not line-by-line.** Module + public class/method docstrings capture non-obvious behavior (see `grounded_rag.py`, `scheduling_context.py`). Match that density — terse, intent-focused.
- **No magic values.** Thresholds/limits are named constants or `Settings` fields (cf. `_ANSWER_SNIPPET_MAX`, `rag_grounding_score_threshold`).
- **Keep modules cohesive and small** — one concern per file under `answerers/` (client, intent, context are separate files). The calendar feature splits the same way: e.g. `calendar_client.py`, `calendar_oauth.py`, `availability.py`, a token repository, an answerer.

### Development Workflow Rules

- **CI gate = `ruff check .` then `pytest` with coverage** (plus `pytest -m e2e`) on every PR and push to main. Both must be green; coverage `fail_under = 100`.
- **BMAD feature-sequential rule** — only one feature epic in implementation at a time; the next starts only after the prior's story tests pass, regression check passes, and demo/acceptance signoff completes (`epics/README.md`). The calendar feature is the next epic (**epic-11**).
- **One PR per story.** Each story is implemented on its own branch and merged via its **own** PR (small, reviewable units mapping 1:1 to the BMAD story cycle: `create-story → dev-story → code-review → PR`). Do NOT batch multiple stories into one PR. The PR's tests + signoff artifact are its acceptance evidence.
- **From Epic 03 onward, every epic integrates with the incident/alerts solution (Epic 02)** — new failure paths (OAuth/token errors, calendar-unavailable) emit incidents/alerts consistent with that, not bespoke logging alone.
- **Docker-first.** `docker compose up --build -d` runs the full stack; SQLite DBs live in `.data/`. New env vars get a `.env.example` entry **and** a `Settings` field. Don't assume a non-Docker runtime.
- **Local dev:** Python 3.11 venv + `pip install -r requirements-dev.txt`. New runtime deps → `requirements.txt`; dev-only → `requirements-dev.txt`.
- **Each story ships with its tests + a signoff artifact** under `_bmad-output/implementation-artifacts/` (cf. epic-01 signoff checklist, `e2e-coverage.md`). Follow the existing epic/story doc shape.

### Critical Don't-Miss Rules

**Anti-patterns (do NOT):**
- ❌ Return `handled=False` when the answerer OWNS the question but the backend failed — that silently leaks a calendar query to the LLM/HITL with wrong context. Degraded = escalate, not fall-through.
- ❌ Wrap `sqlite3` in `async def` — it blocks the event loop. Sync repo + `asyncio.to_thread`.
- ❌ Create an httpx client per call for the stateful calendar/token client — inject one long-lived client.
- ❌ Log tokens, refresh tokens, OAuth secrets, or the encryption key. Log `trace_id`, never the secret.
- ❌ Persist the OAuth client secret / encryption key in SQLite — env via `Settings` only. Per-operator refresh tokens ARE persisted, but encrypted.
- ❌ Hand-roll JWT signing / token refresh — use `google-auth`.
- ❌ Use naive datetimes or `datetime.now()` inside logic — aware + injected clock.

**Security:**
- OAuth callback MUST validate `state` (CSRF) and that the authenticated Telegram operator matches the connect request. Scope consent to **read-only** (`calendar.readonly` / `freebusy`) for the read-only-first phase.
- Encrypt refresh tokens at rest (`cryptography` Fernet); key from env. A leaked refresh token = standing access to the operator's calendar.
- Treat freebusy/event data as potentially confidential — never echo raw event titles into customer answers (availability = busy/free blocks only, not *what* the operator is doing).

**Edge cases / correctness:**
- **Calendar is opt-in per project** (tri-state): (a) **not enabled for the project** → silent no-op `_skip("calendar_not_enabled")`, `handled=False`, pipeline continues — MUST NOT error, escalate, or add latency (cheap project-config check FIRST, before intent detection or any API call); (b) **enabled but operator not connected / token revoked** → helpful "not connected yet" reply and/or escalate to HITL, never a 500; (c) **connected** → compute availability and answer.
- **Calendar connect = enable; disable is explicit.** There is no separate `/calendar_on` command or `/enable` endpoint: a successful `/connect_calendar` OAuth callback flips `enabled=1` and records the connecting operator atomically with the token upsert (existing `project_timezone` / `lookahead_days` preserved on re-connect). `/calendar_off` (operator + admin) flips `enabled=0` while keeping the stored token; re-enable = the operator re-runs `/connect_calendar`. `/disconnect_calendar` deletes the token and is **operator-only** (admin → 403). If the callback's enable write fails after the token upsert, return a 500-class error rather than a misleading success page.
- Concurrent refresh → single-flight `asyncio.Lock`.
- DST + cross-timezone: customer asks in one tz, operator calendar in another — resolve both via `zoneinfo`, compare in UTC.
- A *wrong* "yes, it's free" is worse than escalation — when availability can't be computed confidently, escalate to HITL.
- Service rules (duration, working hours, which days) gate availability ON TOP of freebusy — a slot free on the calendar but outside working hours / on a wrong service-day is NOT available.

**Performance:**
- Cache the access token until near-expiry; refresh only when needed (don't mint per request).
- One freebusy call per availability check (batch the window), not one per candidate slot.

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code in this repo.
- Follow ALL rules exactly. When in doubt, prefer the more restrictive option.
- The Google-Calendar-specific notes apply to the upcoming **epic-11** (availability + scheduling, read-only first). The general rules apply to all work.
- Update this file if new durable patterns emerge.

**For Humans:**
- Keep this file lean and focused on what agents would otherwise miss — not a tutorial.
- Update when the technology stack or core conventions change.
- Review periodically; remove rules that become obvious or obsolete.

Last Updated: 2026-05-22
