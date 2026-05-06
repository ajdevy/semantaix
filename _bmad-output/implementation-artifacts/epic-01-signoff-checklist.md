# Epic 01 Signoff Checklist

## Automated verification

- [ ] `pytest tests/test_bot_gateway_webhook.py tests/test_persistence_repository.py tests/test_epic01_e2e.py`
- [ ] `ruff check .`

## Manual scripted flow

1. Start `bot_gateway` on port `8002`.
2. POST `tests/fixtures/telegram/update_message_text_basic.json` to `/telegram/webhook`.
3. Verify response is `{"status":"accepted", "trace_id":"..."}`.
4. Inspect persistence DB and confirm:
   - one `conversations` row for `telegram_user_id=9001`
   - one `messages` row with `source_message_id=501`
5. Call `/suggest` with sample user text.
6. Verify suggestion-only contract fields:
   - `response_mode = suggestion_only`
   - `is_suggestion_only = true`
   - `guardrails_applied = false`

## Acceptance evidence

- [ ] Test output attached in PR comment
- [ ] Manual run output captured (curl + DB query)
- [ ] Epic 01 stories (`FLE-5`,`FLE-6`,`FLE-7`,`FLE-8`) confirmed in Linear
