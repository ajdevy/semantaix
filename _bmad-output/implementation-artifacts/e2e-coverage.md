# Story-aligned E2E coverage (pytest)

## Definition

“E2E” in this repository means **multi-step integration tests** over the real FastAPI application graph: `fastapi.testclient.TestClient`, **ephemeral SQLite** databases via patched repository paths / env vars, and **mocked external services** (OpenRouter, Telegram send). Tests run in **GitHub Actions** on every PR and push to `main`.

Browser automation is **not** used. Admin HTML in `web_ui` is covered by a minimal HTTP smoke check only until Epic 08 trace UI exists.

## How to run

- Full suite (unit + contract + E2E): `pytest`
- Coverage (same gates as CI): `pytest --cov --cov-config=.coveragerc --cov-report=term-missing`
- E2E marker subset only: `pytest -m e2e`

Markers are declared in `[tool.pytest.ini_options]` in [`pyproject.toml`](../../pyproject.toml).

## Coverage matrix

Each row marks whether a happy path (`H`) and/or an error/incident path (`E`) is covered. Test IDs are pytest node ids; subset under `tests/e2e/` is the e2e-marked suite.

| Epic | Story / area | Scenario | H / E | Primary test ID |
|------|--------------|----------|-------|-----------------|
| 01 | 01-01 | Webhook accepts text update + returns trace | H | `tests/test_bot_gateway_webhook.py::test_webhook_accepts_text_message_and_returns_trace` |
| 01 | 01-02 | Webhook persists conversation + message row | H | `tests/test_bot_gateway_webhook.py::test_webhook_persists_message_rows` |
| 01 | 01-03 | `/suggest` suggestion payload via mocked LLM | H | `tests/test_api_suggest_contract.py::test_suggest_returns_suggestion_payload_on_success` |
| 01 | 01-04 | Webhook persist + `/suggest` cross-service | H | `tests/test_epic01_e2e.py::test_epic01_e2e_webhook_persist_suggest` |
| 01 | 01-04 | Webhook persist + RAG retrieval grounded `/suggest` | H | `tests/e2e/test_e2e_epic01_chain.py::test_epic01_e2e_webhook_persist_then_suggest_with_retrieval` |
| 01 | 01-04 | `/suggest` 503 when OpenRouter key missing | E | `tests/e2e/test_e2e_epic01_chain.py::test_epic01_e2e_suggest_returns_503_when_openrouter_key_missing` |
| 01 | 01-04 | `/suggest` 502 on provider failure | E | `tests/e2e/test_e2e_epic01_chain.py::test_epic01_e2e_suggest_returns_502_on_provider_failure` |
| 02 | 02-02 | Ingest → list → read → ack → resolve → timeline | H | `tests/e2e/test_e2e_epic02_incident_lifecycle.py::test_epic02_full_lifecycle_emit_read_ack_resolve_timeline` |
| 02 | 02-01 | Dedup within window collapses + `deduplicated` event | H | `tests/e2e/test_e2e_epic02_incident_lifecycle.py::test_epic02_dedup_within_window_collapses` |
| 02 | 02-01 | Dedup outside window auto-resolves prior + creates new | E | `tests/e2e/test_e2e_epic02_incident_lifecycle.py::test_epic02_dedup_outside_window_creates_new_and_auto_resolves_prior` |
| 02 | 02-03 | Critical Telegram alert sent + debounced + sent again | H | `tests/e2e/test_e2e_epic02_incident_lifecycle.py::test_epic02_critical_telegram_debounce` |
| 02 | 02-03 | Warning event does not page Telegram | H | `tests/e2e/test_e2e_epic02_incident_lifecycle.py::test_epic02_warning_does_not_send_telegram` |
| 03 | 03-01 | Valid suggestion passes guardrails | H | `tests/e2e/test_e2e_epic03_guardrails.py::test_epic03_valid_suggestion_passes_no_ticket_or_incident` |
| 03 | 03-01 | Low confidence blocks → incident + HITL ticket | E | `tests/e2e/test_e2e_epic03_guardrails.py::test_epic03_low_confidence_blocks_emits_incident_creates_ticket` |
| 03 | 03-01 | Policy violation blocks | E | `tests/e2e/test_e2e_epic03_guardrails.py::test_epic03_policy_violation_blocks` |
| 03 | 03-01 | Too-long response blocks | E | `tests/e2e/test_e2e_epic03_guardrails.py::test_epic03_too_long_response_blocks` |
| 03 | 03-01 | Insufficient content blocks | E | `tests/e2e/test_e2e_epic03_guardrails.py::test_epic03_insufficient_content_blocks` |
| 04 | 04-01 | Blocked suggest → route → resolve | H | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_guardrail_blocked_suggest_then_route_and_resolve` |
| 04 | 04-02 | Bot-authored reply delivered via mocked Telegram | H | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_full_bot_authored_reply_chain` |
| 04 | 04-01 | Route without operator → 503 + incident | E | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_route_without_operator_emits_incident` |
| 04 | 04-02 | Reply missing target chat id → 503 + incident | E | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_reply_missing_target_chat_id_emits_incident` |
| 04 | 04-02 | Reply rejects non-assigned operator → 403 | E | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_reply_rejects_non_assigned_operator` |
| 04 | runtime-config | `/hitl_config` overrides default operator on next blocked suggest | H | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_runtime_config_overrides_default_operator` |
| 05 | 05-02 | RAG ingest then `/suggest` returns matching `retrieval` | H | `tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_then_suggest_includes_retrieval` |
| 05 | 05-01 | Repeated ingest dedup returns zero new chunks | H | `tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_dedup_returns_zero_chunks_second_call` |
| 05 | 05-01 | Multi-source retrieve ranks higher overlap first | H | `tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_retrieve_ranks_higher_overlap_source_first` |
| 05 | 05-01 | Ingest failure → 500 + incident | E | `tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_failure_emits_incident` |
| 05 | 05-03 | Natural-language intent query scores above grounding threshold against catalog chunk | H | `tests/test_rag_repository.py::test_retrieve_buggy_tour_natural_language_query` |
| 05 | 05-03 | Content-token denominator: short on-topic query scores 1.0 | H | `tests/test_rag_repository.py::test_retrieve_score_uses_content_tokens_denominator` |
| 05 | 05-03 | Stopword-only query does not award false-positive score 1.0 | E | `tests/test_rag_repository.py::test_retrieve_stopword_only_query_falls_back` |
| 05 | 05-03 | Grounded-RAG escalation emits structured `grounded_rag_skipped` log with reason | E | `tests/test_answerers_grounded_rag.py::test_weak_retrieval_falls_through` (and seven peer reasons) |
| 06 | 06-02 | Extract → approve → retrievable | H | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_extract_approve_then_retrievable` |
| 06 | 06-02 | Reject not retrievable, status filter shows rejected | H | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_extract_reject_path_not_retrievable` |
| 06 | 06-02 | Approve with edited text publishes edited version | H | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_approve_with_edited_text_publishes_edited_version` |
| 06 | 06-01 | Second extract pass enqueues zero | H | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_extract_idempotent_second_pass_enqueues_zero` |
| 06 | 06-02 | Double-approve returns 409 | E | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_double_approve_returns_409` |
| 06 | 06-02 | Reindex failure → 500 + incident | E | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_reindex_failure_emits_incident` |
| 07 | 07-01 | Backup run → list → restore round-trip via API | H | `tests/e2e/test_e2e_epic07_backup_restore.py::test_epic07_backup_run_then_restore` |
| 08 | 08-01 | `/suggest` writes a queryable `answer_trace` row with retrieval, routing, guardrail, grounding | H | `tests/e2e/test_e2e_epic08_answer_trace.py::test_epic08_suggest_writes_queryable_trace` |
| 08 | 08-02 (partial) | Static admin shell HTTP 200 | H | `tests/e2e/test_e2e_epic08_web_ui_smoke.py::test_epic08_admin_shell_reachable` |
| 08 | 08-02 (partial) | Static alerts shell HTTP 200 | H | `tests/e2e/test_e2e_epic08_web_ui_smoke.py::test_epic08_alerts_shell_reachable` |
| 08 | 08-02 | `/suggest` persists trace; admin trace list + detail render sources/policy/routing/confidence | H | `tests/e2e/test_e2e_epic08_trace_ui.py::test_epic08_trace_visible_in_web_ui` |
| 08 | 08-03 | NL knowledge op: preview → confirm → reindexed; audit log captured | H | `tests/e2e/test_e2e_epic08_nl_ops.py::test_epic08_nl_op_preview_confirm_reindex` |
| 08 | 08-04 | Trace → correction (moderation branch) → approve → retrievable; audit captured | H | `tests/e2e/test_e2e_epic08_correction_loop.py::test_epic08_trace_correction_to_moderation_then_approved_retrievable` |
| 09 | 09-01 | Operator KB intent detection (slash + Russian free-text with lemma fallback) | H | `tests/test_kb_intent.py` |
| 09 | 09-01 | Telegram attachment normalization (document, photo, audio, video, voice, media_group_id) | H | `tests/test_telegram_update_attachments.py` |
| 09 | 09-01 | Telegram file download two-step with size cap and stream-abort cleanup | H/E | `tests/test_telegram_file_download.py` |
| 09 | 09-02 | Local extractors for PDF/DOCX/PPTX/TXT/image (tesseract patched) + soft_wrap | H | `tests/test_operator_uploads_extractors.py` |
| 09 | 09-03 | Audio/video transcription with duration cap + binary SHA-256 streaming | H/E | `tests/test_operator_uploads_extractors.py` |
| 09 | 09-04 | `/knowledge/operator_upload` auto-approval, dedup short-circuit, confidential propagation | H/E | `tests/test_api_operator_upload.py` |
| 09 | 09-04 | Schema migration idempotency on knowledge_moderation_candidates | H | `tests/test_knowledge_moderation_repository_operator.py` |
| 09 | 09-04 | `is_confidential` round-trip through `RagRepository` | H | `tests/test_rag_repository_confidential.py` |
| 09 | 09-04 | Confidential chunk metadata redacted in `GroundedRagAnswerer` while LLM still receives raw text | H | `tests/test_grounded_rag_confidential_redaction.py` |
| 09 | 09-05 | Bot orchestration: webhook → ack → background upload → summary DM | H/E | `tests/test_bot_gateway_kb_command.py` |
| 09 | 09-05 | Scripted live demo: inline → file → dedup → confidential | H | `scripts/epic09_signoff_demo.sh` |

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs Ruff, the **full** pytest run with coverage, then **`pytest -m e2e`** to ensure the E2E marker subset stays green.

## Linear

Use Linear (or any backlog tool) as a **manual** map of what shipped; test names and this matrix should be updated when stories change.
