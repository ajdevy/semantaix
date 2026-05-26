# Story 12.05 — Autonomous client materials dispatch (`/material`)

## Objective
Give the bot the ability to autonomously send a video / photo / PDF to the customer when the funnel reaches a natural media moment (after scoping → pitching, after the equipment question). Register operator-uploaded media via the `/material` slash command (reply mode), pick the right attachment by tag, and dispatch via Telegram with a one-line caption. Failure of a Telegram send falls back to a textual reply within the same turn — never silent.

## Scope

### In Scope
- New api endpoint `POST /sales/dispatch/material` (internal, service-token-gated) `{chat_id, material_id, caption_override?}` → `{ok: true, telegram_file_id_cached: bool}`:
  - Loads the `client_materials` row.
  - Resolves the file: prefer the cached `telegram_file_id` if present; else open `local_path` and upload.
  - Calls `TelegramBotSender.send_video` / `send_photo` / `send_document` (extend the existing sender if needed — the operator `/send` flow already covers the doc path).
  - On a successful Telegram send that returned a new `telegram_file_id`, calls `ClientMaterialsRepository.update_telegram_file_id(...)`.
  - Returns `{ok: true, ...}`; on Telegram error, returns `{ok: false, error_reason: str}` (caller decides fallback behavior).
- `services/api/app/sales/client_materials_selector.py` `ClientMaterialsSelector(*, repo)`:
  - `pick(*, project_id, intent_tags: list[str], purpose: Literal["tour_preview","equipment_gallery","catalog"]) -> ClientMaterial | None`.
  - Calls `repo.pick_by_tags(project_id=..., tags=intent_tags + [purpose])` and picks the top-1 by overlap count; tie-broken by most-recently-updated.
  - Returns `None` when nothing matches (the answerer treats this as a no-op for the media moment; no fabricated reply about media that doesn't exist).
- `SalesPersonaAnswerer` integration:
  - After the scoping → pitching transition completes (all 5 intent fields collected), call `selector.pick(... purpose="tour_preview")`. On hit, dispatch via `POST /sales/dispatch/material`, with a one-line caption from `material.caption` (or a per-stage default if absent). On dispatch failure, fall back to a textual one-liner pitch (so the customer isn't left silent waiting for the media that errored).
  - After an equipment Q&A turn (intent regex: `снаряжение|экипировка|шлем|одежда|что нужно` — added to `data/russian_sales_intent.txt`), call `selector.pick(... purpose="equipment_gallery")` and dispatch.
  - Media dispatch is **fire-and-track**: the answerer's text reply (the pitch sentence) is returned via `AnswerResult` as usual; the media call happens via the api endpoint with the trace_id threaded through so both events land on the same `answer_traces` row.
- New bot_gateway slash-command handler `/material` (operator-or-admin gated, identical to `/service_*`):
  - `/material [caption]` as a **reply** to a Telegram message that has a `video` / `photo` / `document` attachment.
  - Downloads the file via the existing operator-files file_storage pattern (the file path mirrors how `/kb_add` stores uploads — reuse, don't fork). Stores under `.data/sales_materials/<project_id>/<short_id>.<ext>`.
  - Calls `POST /sales/materials` `{project_id, kind, local_path, byte_size, duration_seconds?, caption?, telegram_file_id}` (the bot already has the `telegram_file_id` from the original upload — pre-cache it).
  - Echoes `Добавлено: <kind> id=<row_id> (caption="<caption>")` on success; one-line validation error on a non-reply message or non-media reply target.
- New api endpoints (service-token-gated):
  - `POST /sales/materials` (the bot calls this from the `/material` handler).
  - `GET /sales/materials?project_id=` → `{materials: [ClientMaterial]}` (for `/material_list`).
  - `DELETE /sales/materials/{id}` (for `/material_remove`).
- New bot_gateway commands `/material_list` and `/material_remove <id>` mirroring the `/service_*` shape.

### Out of Scope
- KB-upload → auto-analysis path (story 12.05b — chained after this).
- Editing a material's caption or tags after registration (delete + re-add in v1).
- Sending multiple media in a single turn (v1 picks the top-1 attachment; multi-send is a follow-up).
- Multi-part documents / albums (Telegram `sendMediaGroup`) — v1 uses the single-asset endpoints.
- Retrying a failed Telegram dispatch — per epic exit criteria, the bot falls back to text immediately and does not retry.

## Implementation Notes
- **Reuse, don't fork.** The `/material` upload handler MUST reuse the same storage helper that `/kb_add` uses for `operator_files` (see `services/bot_gateway/app/main.py` around the `/kb_add` flow — the file-fetch + local-write logic stays in one place). The directory `.data/sales_materials/<project_id>/` is the only sales-specific addition.
- **`telegram_file_id` caching seam.** The `POST /sales/dispatch/material` endpoint is the only writer that calls `update_telegram_file_id`. The first send from a `local_path` returns a new `file_id` from Telegram — cache it. Subsequent sends use the cached id (no disk read). The cache lives in the same row as the original registration; no separate cache table.
- **Caption length cap.** Telegram caps captions at 1024 chars (video) / 1024 chars (photo) — enforce a 200-char ceiling in the api endpoint (rejection returns one-line "caption too long" to the operator). Per-epic copy preference: captions are one short sentence, not a paragraph.
- **Persona name not embedded in caption.** The caption is operator-authored — the bot does NOT prepend the persona name (`{persona}: …`) to the caption. The persona influences only the surrounding scoping/pitching text.
- **Fire-and-track tracing.** Both the textual pitch and the media dispatch share a `trace_id`; the dispatch endpoint logs `sales_material_dispatched` with `{trace_id, material_id, kind, telegram_file_id_cached_now}`. NEVER log `telegram_file_id` itself (per epic exit criteria — log-capture test enforces).
- **No fabricated media.** When `selector.pick(...)` returns `None`, the answerer emits the textual pitch only; never says "вот видео" without actually dispatching one.
- **`POST /sales/dispatch/material` is internal-only.** The route is registered with the same service-token Bearer dependency as the other internal `/sales/*` endpoints; never exposed externally.

## Test Plan
### Unit
- `tests/test_sales_client_materials_selector.py` — `pick` returns the best overlap; ties broken by `updated_at` desc; `None` on empty / no overlap; the `purpose` token is always included in the lookup tags.
- `tests/test_api_sales_dispatch_material_endpoint.py` — cached `telegram_file_id` path (sends without reading disk); fresh-upload path caches the returned id; Telegram error → endpoint returns `{ok: false}` and does NOT call `update_telegram_file_id`; missing service-token → 401; unknown material_id → 404.
- `tests/test_api_sales_materials_endpoints.py` — `POST /sales/materials` round-trip with all optional fields; caption > 200 chars rejected; `GET` lists active; `DELETE` flips `is_active`.
- `tests/test_bot_gateway_material_command.py` — `/material` on a reply-to-video extracts file metadata and posts to api; `/material` on a non-reply or non-media-reply returns the one-line usage error; caption arg overrides the original Telegram caption.
- `tests/test_sales_persona_answerer_media_moment.py` — after scoping completes, the answerer dispatches the picked tour-preview material (asserts `POST /sales/dispatch/material` called with the right args via a mocked api client); on `selector.pick` returns `None`, no dispatch call and the textual pitch is sent alone; on dispatch returning `{ok: false}`, a textual fallback reply is added in the same turn.

### Integration
- `tests/test_sales_material_dispatch_pipeline.py` — full inbound message → answerer pitches → dispatch endpoint sends via a stubbed `TelegramBotSender` → `telegram_file_id` cached in the repo (verified by reading the row back).

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_material_dispatch.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-05")`):
  - Operator registers a video via `/material` (reply to a media message, stubbed Telegram).
  - Customer completes scoping → bot text-pitches + dispatches the registered video; `telegram_file_id` cached on the row.
  - Second customer (different chat) reaches the same moment → bot dispatches using the cached `file_id` (no second disk read; asserted via a spy on the file-open seam).

## Manual Verification
1. As the operator: forward an MP4 to the bot, then reply to it with `/material`. Expect `Добавлено: video id=1`.
2. Repeat with a photo and `/material Гора Ачишхо на закате` — caption arg used.
3. `/material_list` shows both rows.
4. As a customer: walk through the scoping turns; once all five fields are collected, expect the video to arrive with the configured caption (or the textual pitch alone if no matching `tour_preview` material exists).

## Done Criteria
- 100% coverage on `client_materials_selector.py`, `POST /sales/dispatch/material`, the `/sales/materials` CRUD endpoints, the `/material*` bot_gateway handlers, and the new media-moment branches of `sales_persona_answerer.py`.
- `ruff check .` passes; E2E green.
- `telegram_file_id` cached on first send; never logged.
- Telegram send error → textual fallback within the same turn (no retries, no silent bot).
- File storage path mirrors the existing `operator_files` pattern; no duplicate fetch/store helper introduced.
- No fabricated "вот видео" reply when no matching material exists.
