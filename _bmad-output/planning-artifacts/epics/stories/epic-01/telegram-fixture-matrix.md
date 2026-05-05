# Epic 01 Telegram Fixture Matrix (Web-Verified)

Source basis:
- Telegram Bot API `Update` object and webhook behavior (`setWebhook`)
- Update payloads are JSON-serialized; at most one optional update field is present.

## Fixtures and Expected Outcomes

| Fixture | Intent | Expected Status | Persistence Expectation | Logging/Trace Expectation |
| --- | --- | --- | --- | --- |
| `update_message_text_basic.json` | Standard text message | `200/202` accepted | conversation/message created or appended | `trace_id` present, normalized message logged |
| `update_message_text_empty.json` | Empty/whitespace text | `200/202` accepted with no suggestion OR explicit validation `4xx` (choose one policy and keep consistent) | no invalid text persisted as actionable message | explicit reason logged (`empty_text`) |
| `update_duplicate_update_id.json` | Retry/duplicate delivery | `200/202` accepted idempotently | no duplicate message row (`source_message_id` unique) | dedup/idempotent event logged |
| `update_malformed_missing_core.json` | Invalid payload structure | `4xx` rejected | no persistence | validation failure logged with trace |
| `update_callback_query_valid.json` | Valid non-message update type | `200/202` accepted and safely ignored in Epic 01 | no message persistence for suggestion flow | unsupported update type logged (non-fatal) |
| `update_edited_message_valid.json` | Edited message update type | `200/202` accepted and safely ignored in Epic 01 | no suggestion-flow persistence unless policy explicitly includes edited messages | update type handling logged |
| `update_non_text_message_photo.json` | Message without `text` | `200/202` accepted and safely ignored OR explicit `4xx` by policy | no text-message persistence for suggestion flow | non-text handling logged |

## Required Contract Notes

1. All accepted payloads must include a generated or propagated `trace_id` in logs.
2. Duplicate handling must be deterministic via `source_message_id` idempotency.
3. Unsupported but valid Telegram update types must not crash the pipeline.
4. Policy for empty/non-text handling must be explicit and test-asserted.
