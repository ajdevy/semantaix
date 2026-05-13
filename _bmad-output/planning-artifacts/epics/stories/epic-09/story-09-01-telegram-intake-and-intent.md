# Story 09.01 — Telegram intake: attachment normalization + intent detection + file download

## Objective
Extend `bot_gateway` to (a) recognize `/kb_add` plus Russian free-text intent in operator messages, (b) accept Telegram `document/photo/voice/audio/video` attachments without dropping them, and (c) download the binary to a shared volume — all gated to the effective operator username.

## Scope

### In Scope
- Extend `services/bot_gateway/app/telegram_update.py`: add `caption`, `media_group_id`, `attachments: tuple[TelegramAttachment, ...]` to `NormalizedTelegramMessage`. Keep `text=""` allowed when attachments are present. For `photo` arrays keep the largest size only.
- New `services/bot_gateway/app/kb_intent.py`: pure function `detect_kb_intent(*, text, caption, normalizer) -> KbIntent | None`. Slash form: regex `^\s*/kb_add(?:\s+(confidential))?\s*$`. Free-text: literal substring fast-path against casefolded body, lemma-sequence fallback using `services.api.app.russian_text.RussianNormalizer.lemmas`. Phrases load once from `data/russian_kb_intent_phrases.txt`. Confidential keywords: `конфиденциально|приватно|секрет|не для цитирования`. Returns `KbIntent(confidential, mode, cleaned_text)`.
- New `services/bot_gateway/app/telegram_file_download.py`: `TelegramFileDownloader(*, bot_token, storage_dir, max_bytes, http_client_factory=httpx.AsyncClient)` with `async download(file_id, suggested_extension) -> DownloadedFile`. Two-step: `GET /bot{TOKEN}/getFile` → reject if `file_size > max_bytes`, then stream `GET /file/bot{TOKEN}/{file_path}` to `Path(storage_dir) / f"{uuid4()}.{ext}"`.

### Out of Scope
- Extraction (story 09.02–09.03), API dispatch (09.04), confidential metadata redaction (09.04), end-to-end bot orchestration (09.05).

## Implementation Notes
- Operator gating: only `_effective_operator_username()` may trigger; non-operator returns `{"status":"ignored","reason":"unauthorized_kb"}`.
- Existing operator-reply path is untouched — KB handling runs *before* it but after `/hitl_config`.
- Seed phrases (UTF-8, one per line) in `data/russian_kb_intent_phrases.txt`:
  ```
  добавь в базу
  добавь в базу знаний
  сохрани в kb
  сохрани в базу
  запомни это для базы знаний
  положи в базу знаний
  загрузи в kb
  ```

## Test Plan

### Unit
- `tests/test_kb_intent.py` — slash forms, each phrase, inflected variants, confidentiality keywords, negative "добавь молока в магазин", `cleaned_text` strips trigger.
- `tests/test_telegram_update_attachments.py` — document only, photo array picks largest, voice with caption, `media_group_id` passthrough, malformed payload returns `None`.
- `tests/test_telegram_file_download.py` — `httpx.MockTransport` for `getFile` + CDN, oversize rejection before second call, missing `file_path` raises, happy-path writes to `tmp_path`.

### Integration
None in this story (covered by 09.05 end-to-end).

## Automated E2E verification
Deferred to 09.05.

## Manual Verification
1. Send `/kb_add` to the bot from the operator's Telegram account with no attachment — until 09.05 lands, only confirm `detect_kb_intent` returns the expected `KbIntent` via a Python REPL.
2. Repeat from a non-operator username — confirm authorization rejection at the handler level.

## Done Criteria
- All unit tests pass.
- 100% coverage on touched modules in `services/bot_gateway/`.
- Lemma fallback exercised by an inflection test (e.g. "добавь это в базе знаний" matches "добавь в базу знаний").
- `ruff check .` passes.
