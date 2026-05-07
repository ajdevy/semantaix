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

| Epic | Story / area | Scenario | Primary test ID |
|------|----------------|----------|----------------|
| 01 | 01-01 | Telegram webhook accepts text update and returns trace | `tests/test_bot_gateway_webhook.py::test_webhook_accepts_text_message_and_returns_trace` |
| 01 | 01-02 | Persisted conversation + message row after webhook | `tests/test_bot_gateway_webhook.py::test_webhook_persists_message_rows` |
| 01 | 01-03 | `/suggest` suggestion payload via mocked LLM | `tests/test_api_suggest_contract.py::test_suggest_returns_suggestion_payload_on_success` |
| 01 | 01-04 | Webhook persistence + `/suggest` cross-service | `tests/test_epic01_e2e.py::test_epic01_e2e_webhook_persist_suggest` |
| 02 | 02-02 | Incident ingest → timeline → read → acknowledge → resolve | `tests/test_api_incidents_contract.py::test_incident_read_ack_resolve_and_timeline` |
| 03 | (with 04) | Guardrails block weak LLM output → escalation path | `tests/test_api_hitl_contract.py::test_invalid_suggest_creates_and_assigns_hitl_ticket` |
| 04 | 04-01 | Blocked suggest → route → resolve | `tests/e2e/test_e2e_epic04_hitl_journey.py::test_epic04_guardrail_blocked_suggest_then_route_and_resolve` |
| 04 | 04-02 | Invalid suggest creates HITL ticket + operator assignment | `tests/test_api_hitl_contract.py::test_invalid_suggest_creates_and_assigns_hitl_ticket` |
| 04 | 04-02-reply | Operator reply delivered via mocked Telegram sender | `tests/test_api_hitl_contract.py::test_hitl_reply_delivered_as_bot_authored` |
| 04 | runtime config | Admin `/hitl_config` on bot gateway updates runtime operator + chat | `tests/test_bot_gateway_webhook.py::test_admin_can_configure_hitl_contact_via_command` |
| 05 | 05-02 | RAG ingest then `/suggest` returns matching `retrieval` | `tests/e2e/test_e2e_epic05_rag_suggest.py::test_epic05_rag_ingest_then_suggest_includes_retrieval` |
| 06 | 06-02 | `/knowledge/extract` → approve candidate → retrievable in RAG | `tests/e2e/test_e2e_epic06_knowledge_pipeline.py::test_epic06_extract_approve_then_retrievable` |
| 07 | 07-01 | Backup run → list → restore round-trip via API | `tests/e2e/test_e2e_epic07_backup_restore.py::test_epic07_backup_run_then_restore` |
| 08 | 08-01 | `/suggest` writes a queryable `answer_trace` row with retrieval, routing, guardrail, grounding | `tests/e2e/test_e2e_epic08_answer_trace.py::test_epic08_suggest_writes_queryable_trace` |
| 08 | 08-02 (smoke) | Static admin shell HTTP 200 | `tests/e2e/test_e2e_epic08_web_ui_smoke.py::test_epic08_admin_shell_reachable` |
| 08 | 08-02 | `/suggest` persists trace; admin trace list + detail render sources/policy/routing/confidence | `tests/e2e/test_e2e_epic08_trace_ui.py::test_epic08_trace_visible_in_web_ui` |
| 08 | 08-03, 08-04 | NL knowledge ops, correction loop | **Deferred** until APIs and UI beyond static shell exist |

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs Ruff, the **full** pytest run with coverage, then **`pytest -m e2e`** to ensure the E2E marker subset stays green.

## Linear

Use Linear (or any backlog tool) as a **manual** map of what shipped; test names and this matrix should be updated when stories change.
