# Epic 01 Signoff Checklist

## Automated verification

Use **Python 3.11** and a local venv (same as CI); `python3` on macOS Homebrew may be 3.14+ without project deps:

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

Single entry point (recommended): **lint + full test suite + live demo**:

```bash
bash scripts/run_all_epic_feature_signoffs.sh
```

Or run CI steps separately:

- [ ] `ruff check .`
- [ ] `pytest tests/test_bot_gateway_webhook.py tests/test_persistence_repository.py tests/test_epic01_e2e.py`
- [ ] CI parity is `pytest --cov --cov-config=.coveragerc --cov-report=term-missing` (runs all tests).

## Manual scripted flow

Run:

```bash
bash scripts/epic01_signoff_demo.sh
```

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
   - `guardrails_applied = true`

## Acceptance evidence

- [ ] Test output attached in PR comment
- [ ] Manual run output captured (curl + DB query)
- [ ] Epic 01 stories (`FLE-5`,`FLE-6`,`FLE-7`,`FLE-8`) confirmed in Linear
