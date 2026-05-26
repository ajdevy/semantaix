# Story 12.05c — KB-upload → automatic services extraction

## Objective
When the operator uploads a file to the knowledge base via the existing `/kb_add` flow, automatically scan its extracted text for **service-shaped entries** (tour names, activity offerings, etc.) and populate the `services` table accordingly. This is the second of three input paths for services: (1) slash commands (12.02), (2) natural-language operator dialog (12.02b), (3) **automatic extraction from KB files (this story)**. Runs as a third post-upload hook alongside the materials analyzer (12.05b); both share the same `operator_files_view` text seam but write to different tables.

## Scope

### In Scope
- `services/api/app/sales/services_extractor.py` `ServicesExtractor(*, openrouter, operator_files_view, services_repo)`:
  - `async def extract_and_register(*, project_id: int, operator_file_short_id: str, now: datetime) -> ExtractionOutcome`.
  - Reads `extracted_text` and file metadata from `operator_files_view`. Truncates to the first 6000 chars (slightly larger than the materials analyzer's 4000 — service catalogs span a few pages of a PDF).
  - Calls `OpenRouterClient.complete_json(...)` with `system_prompts/operator_services_extractor.txt` (Russian; instructs the LLM to scan the text for distinct service / tour / activity offerings, return each as `{name, description}`). Response schema: `{"services": [{"name": str, "description": str | null}], "reason": str}`. The `reason` is a one-line explanation when the LLM judges the file has no services (e.g. "personal letter", "invoice"); empty `services` array is a valid response.
  - For each extracted entry:
    - Calls `ServicesRepository.find_by_name(project_id, name)` (case-insensitive, 12.01).
    - **No collision:** `ServicesRepository.add(project_id=..., name=..., description_md=description, tags=[], now=now)` — `is_active=1`.
    - **Existing service:** soft-skip — do not overwrite the operator's manually-crafted description. (The "describe" NL flow in 12.02b is the explicit way to update an existing service.)
  - Returns `ExtractionOutcome(added: list[AddedService], skipped_existing: list[str], reason: str)` where `AddedService(service_id, name)`.
  - **Skip-if-confidential.** When `operator_files_view` marks the file `is_confidential=True`, short-circuit before the LLM call: `ExtractionOutcome(added=[], skipped_existing=[], reason="confidential_kb_file")`. Confidential files MUST NOT contribute to the public-facing service catalog.
- Frozen dataclasses `ExtractionOutcome` and `AddedService`.
- New api endpoint `POST /sales/services/extract-from-kb-file` (internal, service-token-gated) `{project_id, operator_file_short_id}` → `ExtractionOutcome`-shaped JSON. The bot_gateway KB-upload hook calls this.
- **bot_gateway hook extension** (the same `/kb_add` ack flow that 12.05b extended):
  - After the existing KB ack, the hook fans out two parallel calls via `asyncio.gather`:
    1. `POST /sales/materials/analyze-kb-file` (12.05b — client materials).
    2. `POST /sales/services/extract-from-kb-file` (this story).
  - Each returns its own optional one-line note. The bot appends notes in the same message, in this order:
    - `📎 Добавлен в материалы для клиентов (id=42).` (from 12.05b, if `registered=True`)
    - `📦 Услуги добавлены: Медовеевка Лайт, Каньонинг.` (from this story, if `added` is non-empty)
  - If both are empty / non-applicable: only the existing KB ack is sent. Operator never sees a second message about a file that yielded nothing.
  - Each hook is independent: an exception in one does NOT block the other (each is wrapped in `try/except` per the existing hook pattern). Failure of services extraction is silent to the operator + logged as `sales_services_extract_failed`.
- `system_prompts/operator_services_extractor.txt` — Russian system prompt. Includes:
  - Framing: the LLM is reading a document that may describe one or more services / tours / activities offered to clients.
  - Service criteria: a service is a distinct **offering** with a recognizable **name** that clients can ask about — "Медовеевка Лайт", "Ивановский водопад", "каньонинг". NOT: a price line, a piece of equipment, a generic phrase ("горные туры"), a person's name.
  - Strict JSON-out contract with the `services: [{name, description}]` shape; empty array is valid.
  - Description guidance: one short sentence per service, in Russian, extracted from the file content; null if the file doesn't describe what the service is.
  - The `reason` field is operator-facing (logged, not shown to customers); short Russian sentence.

### Out of Scope
- Retroactive extraction on existing KB files (per epic out-of-scope — same rule as 12.05b).
- Tag extraction (operator manages tags via 12.02 / 12.02b).
- Pricing extraction into a separate "prices" table — pricing stays KB-RAG-driven per 12.04.
- Overwriting an existing service's description on file upload (use 12.02b NL `describe` to do that explicitly).
- Multi-language extraction (Russian-first; English-named services in a Russian file out of scope for v1).
- Manual re-trigger command (`/services_extract <file_id>`) — explicitly not shipped; aligns with 12.05b's "no retroactive scan" stance.

## Implementation Notes
- **Runs alongside, not nested under, the materials analyzer.** The two hooks are sibling post-upload steps. The bot_gateway orchestrates both via `asyncio.gather` so a single KB upload triggers at most two LLM calls + at most one combined note message. Don't chain them — they have unrelated outputs.
- **Idempotency on `(project_id, name)`.** The extractor relies on `ServicesRepository.find_by_name` to skip duplicates. Two KB uploads of the same catalog → second one extracts the same names → all are skipped (returned in `skipped_existing`). No duplicate rows.
- **Never overwrite a manually-crafted description.** The 12.02 / 12.02b paths are the operator's "I know what I want this to say" voice; the extractor is best-effort. If the operator added "каньонинг" with description "Каньонинг — это…" and a file upload then extracts "каньонинг" with description "Активный спорт на воде", the upload-extracted description is dropped, not merged, not overwritten.
- **LLM call is JSON-structured** with the same patterns as 12.05b. Schema-violation → `ExtractionOutcome(added=[], reason="llm_schema_violation")`; logged; no exception.
- **Confidential files never contribute.** Confidential KB uploads (`/kb_add confidential`) short-circuit before the LLM call. Asserted by both unit and integration tests.
- **No service descriptions in logs.** The extractor logs `sales_services_extracted_count` with `{trace_id, project_id, count_added, count_skipped}` — never the names or descriptions verbatim (the operator's catalog content could contain pricing intelligence they don't want in operational logs).
- **Cost discipline.** 6000-char truncation + at most one LLM call per upload. If the operator uploads a 200-page catalog, the extractor sees the first ~10 pages — that's typically enough for an offerings overview.
- **Operator can always edit.** If the extractor misses a service or names it badly, the operator uses 12.02 (`/service_add` / `/service_remove`) or 12.02b (NL) to fix it manually. The extractor's role is "best-effort first pass", not "authoritative source".

## Test Plan
### Unit
- `tests/test_services_extractor_happy_path.py` — fake LLM returns 3 services → `services_repo.add` called 3 times; `ExtractionOutcome.added` has 3 entries; `skipped_existing` empty.
- `tests/test_services_extractor_skips_existing.py` — pre-seed two services in the repo; LLM returns 3 (two of which exist) → only 1 new `add` call; `skipped_existing` lists the two pre-existing names.
- `tests/test_services_extractor_empty_extraction.py` — LLM returns `services: []` with a reason → no repo writes; `ExtractionOutcome(added=[], reason="...")`.
- `tests/test_services_extractor_confidential.py` — `operator_files_view` returns `is_confidential=True` → no LLM call (asserted via spy); `reason="confidential_kb_file"`.
- `tests/test_services_extractor_schema_violation.py` — malformed JSON → `ExtractionOutcome(added=[], reason="llm_schema_violation")`, logged.
- `tests/test_services_extractor_truncation.py` — input text > 6000 chars → first 6000 chars passed to the LLM (captured prompt args assertion).
- `tests/test_api_sales_extract_from_kb_file_endpoint.py` — endpoint round-trip; service-token-gated (401 without).

### Integration
- `tests/test_bot_gateway_kb_upload_dual_hook.py` — full `/kb_add` happy path with stubbed `ApiClient`: both 12.05b and 12.05c return non-empty outcomes → the operator gets a single message with the existing KB ack + both append lines, in the documented order. One returning empty → only the non-empty line appears. Both empty → no extra lines, just the bare KB ack.
- `tests/test_bot_gateway_kb_upload_extract_failure_does_not_block_materials.py` — services-extract call raises an exception → materials line still posted; failure logged; KB ack still sent.
- `tests/test_bot_gateway_kb_upload_confidential_skips_both_hooks.py` — `/kb_add confidential` → neither hook contributes; KB ack only.

## Automated E2E verification
- `tests/e2e/test_e2e_epic12_kb_auto_services.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-05c")`):
  - Operator `/kb_add` a fixture catalog PDF (`tests/fixtures/sales/tour_catalog.pdf`) describing three tours → after the call, three rows in `services` + KB ack includes `📦 Услуги добавлены: ...` listing all three.
  - Operator `/kb_add` a second file describing two tours (one new, one already present) → only one new row added; ack mentions only the new one.
  - Operator `/kb_add confidential` a tour-shaped file → no services rows added; no `📦` line in the ack.
  - Operator `/kb_add` a non-service file (e.g. `internal_invoice.pdf`) → empty `added`; no `📦` line.

## Manual Verification
1. Upload «Туры на Багги23.pdf» via `/kb_add`. Expect the KB-ingest ack PLUS `📦 Услуги добавлены: <names>` listing the extracted service names. Confirm via `/service_list`.
2. Upload the same file again. Expect no `📦` line (everything was already there); `/service_list` shows the same rows.
3. Upload a tour-catalog file with `/kb_add confidential`. Expect no `📦` line; `/service_list` unchanged.
4. Upload a non-offerings file (e.g. an employee schedule). Expect no `📦` line.
5. Manually `/service_remove` a row that was auto-extracted, then re-upload the source file. Expect the service to be re-added (the soft-deleted row is bypassed by `find_by_name` looking at active-only).

## Done Criteria
- 100% coverage on `services_extractor.py`, the new api endpoint, and the bot_gateway dual-hook orchestration glue.
- `ruff check .` passes; E2E green.
- Confidential KB files never contribute to the `services` table.
- Extractor never overwrites a manually-crafted description (idempotency on `(project_id, name)` via `find_by_name`).
- Hook failure is silent to the operator and never blocks the KB ack or the materials hook (each wrapped in try/except).
- LLM call is structured JSON; schema-violation logged and returns empty `added`, no exception.
- Operator-facing message format: existing KB ack first, then `📎 ...` materials line (if any), then `📦 ...` services line (if any).
- No service names or descriptions in logs — only counts.
