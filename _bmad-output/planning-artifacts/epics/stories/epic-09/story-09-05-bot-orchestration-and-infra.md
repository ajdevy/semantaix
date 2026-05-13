# Story 09.05 — Bot orchestration, Docker/infra, signoff script

## Objective
Wire stories 09.01–09.04 together: bot recognizes intent, acks immediately, downloads files in a background task, POSTs to api, DMs a summary, surfaces failures — plus Docker, volumes, and a signoff script.

## Scope

### In Scope
- `services/bot_gateway/app/api_client.py`: add `async submit_operator_upload(...)` posting to `/knowledge/operator_upload` with `timeout=settings.operator_upload_api_timeout_seconds` (default 120 s).
- `services/bot_gateway/app/main.py`: inject `_handle_kb_command(normalized, background_tasks) -> dict | None` between `/hitl_config` handling and customer/operator routing. Add `BackgroundTasks` parameter to `telegram_webhook`. Ack synchronously via a thin `_send_dm(chat_id, text)` using `httpx.AsyncClient(timeout=15)`. Russian acks: `"Принял N файл(а), обрабатываю…"` or `"Принял текст, добавляю в базу…"`. Background task processes each attachment serially: download → submit → accumulate result → final summary DM `"✅ Добавлено в базу: {ok} файл(а), {sum_chunks} чанков, {n_conf} помечен(о) confidential."` plus `"⚠️ Не удалось обработать {name}: {reason}"` per failure.
- `platform_common/settings.py`: add `operator_upload_max_bytes=20*1024*1024`, `operator_upload_max_audio_seconds=900`, `operator_upload_storage_dir=".data/operator_uploads"`, `operator_kb_intent_phrases_path="data/russian_kb_intent_phrases.txt"`, `faster_whisper_model_size="base"`, `faster_whisper_compute_type="int8"`, `faster_whisper_cache_dir="/app/.cache/whisper"`, `operator_upload_api_timeout_seconds=120`.
- `services/api/Dockerfile`: install system packages: `apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*`. Also `COPY data ./data` (fixes a pre-existing latent gap — guardrails/profanity paths reference `data/` outside the container today).
- `services/bot_gateway/Dockerfile`: `COPY data ./data` (no ffmpeg needed here).
- `docker-compose.yml`: add named volumes `operator_uploads` (shared by api + bot_gateway at `/app/.data/operator_uploads`) and `whisper_cache` (api-only at `/app/.cache/whisper`).
- `.gitignore`: append `.data/operator_uploads/`.
- New `scripts/epic09_signoff.sh`: curl + sqlite assertions for happy path, confidential round-trip, inline-text, dedup; wired into `scripts/run_all_epic_feature_signoffs.sh`.

### Out of Scope
- Editing or deleting previously uploaded knowledge.
- Multi-operator UX.

## Implementation Notes
- For inline-text intent with no attachments, bot calls `submit_operator_upload(..., source_file_type="inline_text", inline_text=intent.cleaned_text)`.
- Operator-only gating: same `_effective_operator_username()` check as the existing operator-reply path.
- Size guard runs in `TelegramFileDownloader` before the actual binary fetch, so oversize files cost only a single `getFile` call.

## Test Plan

### Unit
- `tests/test_bot_gateway_kb_command.py`: operator `/kb_add` with a document triggers ack + schedules background task; non-operator → `{"status":"ignored","reason":"unauthorized_kb"}`; oversize file rejected with Russian message; inline-text path routes correctly; per-file failure produces a warning line in the summary DM.

### Integration / E2E
- `tests/e2e/test_e2e_epic09_bot_to_answer.py` (`@pytest.mark.e2e`): full path from a fake Telegram update → bot_gateway → api → RAG → `/conversations/inbound` for the customer question, including a confidential round-trip verifying redacted answer-trace metadata.

## Automated E2E verification
- `tests/e2e/test_e2e_epic09_*.py` rows added to `_bmad-output/implementation-artifacts/e2e-coverage.md`.

## Manual Verification
1. `docker compose up --build -d`; confirm all services healthy.
2. From the operator's Telegram account, send a real PDF with caption `/kb_add` and confirm the ack DM + summary DM.
3. Send the same PDF again — summary DM reports dedup.
4. Send a 30-second Russian voice note with caption `сохрани в kb` — confirm whisper transcript ends up in RAG.
5. Send an image of a notice with caption `/kb_add confidential` — confirm chunks have `is_confidential=1` and answer trace metadata is redacted.
6. Send a customer question matching the new content via another Telegram account; confirm a grounded answer.
7. Run `bash scripts/epic09_signoff.sh`.

## Done Criteria
- All E2E and unit tests pass.
- Coverage 100% on touched modules.
- `ruff check .` passes.
- `epic09_signoff.sh` exits 0.
- Manual checks 1–7 pass.
