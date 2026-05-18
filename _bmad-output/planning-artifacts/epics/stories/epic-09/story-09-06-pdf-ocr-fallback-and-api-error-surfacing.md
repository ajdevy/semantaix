# Story 09.06 — PDF OCR fallback + API error surfacing

## Objective
Close two production gaps revealed when an operator uploaded a slide-deck PDF
("Презентация для Реинфо.pdf") alongside a text PDF in a single Telegram media
group: the slide-deck PDF failed with an opaque `422` (no visible reason in
the operator DM), and even with a clear reason the file could not ingest
because `pypdf` cannot extract text from image-only or vector-glyph PDFs.

Both gaps are augmentations of stories 09.02 (extractors) and 09.05 (bot
orchestration); neither changes the public surface of `/knowledge/operator_upload`.

## Scope

### In Scope
- `services/api/app/operator_uploads/extractors.py`:
  - `extract_pdf(path)` tries `pypdf` first; if the combined `.strip()` is empty,
    falls back to `_ocr_pdf(path)` which renders each page with
    `pypdfium2.PdfDocument(...).render(scale=150/72)`, converts to PIL via
    `bitmap.to_pil()`, and OCRs with
    `pytesseract.image_to_string(image, lang="rus+eng")` (the same call already
    used by `extract_image`).
  - `_ocr_pdf` raises `ExtractionError("pdf_too_many_pages_for_ocr")` when the
    page count exceeds `settings.operator_upload_pdf_ocr_max_pages` (default 50).
  - New module-private constant `_PDF_OCR_DPI = 150`; ~150 DPI is the
    sweet-spot for tesseract on slide-deck text without inflating render time.
- `platform_common/settings.py`:
  - `operator_upload_pdf_ocr_max_pages: int = 50` (env-overridable as
    `OPERATOR_UPLOAD_PDF_OCR_MAX_PAGES`).
- `requirements.txt`:
  - Add `pypdfium2==4.30.0`. Pure-Python wheels for linux/macos/windows — no
    system poppler dependency required, no Dockerfile change.
- `services/bot_gateway/app/api_client.py`:
  - New `ApiError(httpx.HTTPStatusError)` carrying `.detail` (parsed from the
    response JSON body when present). Subclasses `HTTPStatusError` so existing
    `except httpx.HTTPStatusError` sites in `operator_resolver.py`,
    `admin_commands.py`, `admin_nl_dialog.py` keep working unchanged.
  - New `_raise_for_status(response)` helper replaces every direct
    `response.raise_for_status()` in `_post`, `_get`, `_patch`,
    `fetch_file_inspect`, and `search_files`.
- `services/bot_gateway/app/main.py`:
  - `_process_operator_upload` adds an `except ApiError as exc` branch ahead of
    the generic `except Exception` so the api's `detail` (e.g. `"empty_text"`,
    `"pdf_too_many_pages_for_ocr"`) survives into the failure tuple instead of
    being collapsed into the opaque httpx message string.
  - `_friendly_failure_reason` gains an `_API_DETAIL_FRIENDLY` map of
    Russian-localized strings for `empty_text`, `unsupported_source_file_type`,
    `missing_stored_binary_path`, `binary_not_found`, `empty_inline_text`,
    `zip_corrupt`, `zip_too_many_members`, `zip_too_large`,
    `nested_zip_not_supported`, `pdf_too_many_pages_for_ocr`, `audio_too_long`,
    `ffprobe_no_duration`, `operator_upload_failed`. Both `"api_failed:<code>"`
    (attachment path) and bare `"<code>"` (inline-text path) are recognized.

### Out of Scope
- LLM-based PDF understanding (OCR-only fallback is intentional — zero external
  API cost, same as story 09.02).
- Re-extraction or re-OCR of previously failed uploads in the database (any
  operator can re-send the file once this story ships).
- Multipart `/knowledge/operator_upload_multipart` does not need changes — it
  shares the same `_perform_operator_upload` path.
- Per-language OCR tuning beyond the existing `lang="rus+eng"`.

## Implementation Notes
- The OCR fallback runs only when `pypdf` returns an empty string after
  `.strip()` — text PDFs (the 99% case) take the cheap path. Verified by a
  monkeypatched `pdfium.PdfDocument` that raises `AssertionError` if the
  fallback ever triggers for a text PDF.
- `_ocr_pdf` closes each page and the document via `try / finally` to avoid
  pdfium handle leaks during long iterations.
- `ApiError` extracts `detail` only when the response body parses as JSON
  *and* the field is present; non-JSON bodies (HTML 5xx pages) yield
  `detail=None`. Non-string detail values are coerced via `str()` so FastAPI
  validation errors (`{"detail": {"loc": [...], "msg": "..."}}`) still surface
  something useful.
- The bot's existing `_redact_token` wrapper is preserved on the surfaced
  detail string so a hypothetical bot-token leak in an API error message
  cannot reach the operator DM.

## Test Plan

### Unit — extractors (`tests/test_operator_uploads_extractors.py`)
- `test_extract_pdf_uses_pypdf_when_text_present`: monkeypatched
  `pdfium.PdfDocument` raises if called — proves the OCR path is skipped when
  pypdf succeeds.
- `test_extract_pdf_falls_back_to_ocr_for_image_only_pdf`: builds an
  image-only PDF in-test via `PIL.ImageFont.load_default(size=48)` rendered
  into a PIL image, saved as PDF via `Image.save(..., format="PDF")`; mocks
  `pytesseract.image_to_string` to return a fixed Russian string and asserts
  it appears in the result and that `lang == "rus+eng"`.
- `test_extract_pdf_ocr_respects_page_cap`: 4-page image-only PDF +
  `operator_upload_pdf_ocr_max_pages=2` setting → `ExtractionError` with
  `reason == "pdf_too_many_pages_for_ocr"`.
- `test_extract_pdf_returns_empty_when_ocr_yields_nothing`: mocks tesseract to
  return whitespace, asserts empty string (so the `_perform_operator_upload`
  empty-text 422 path still triggers for truly text-less PDFs).

### Unit — api_client (`tests/test_bot_gateway_api_client.py`)
- `test_post_raises_api_error_with_detail_when_json_body`: response 422
  `{"detail": "empty_text"}` → `ApiError(detail="empty_text",
  status_code=422)` and `isinstance(exc, httpx.HTTPStatusError) is True`.
- `test_post_api_error_detail_is_none_when_body_not_json`: 500 + `text/html`
  body → `detail is None`.
- `test_post_api_error_detail_is_none_when_detail_field_missing`: 400 +
  `{"error": "nope"}` → `detail is None`.
- `test_post_api_error_stringifies_non_string_detail`: FastAPI-style
  `{"detail": {"loc": [...], "msg": "field required"}}` → detail contains
  `"field required"`.
- `test_get_raises_api_error_with_detail`,
  `test_patch_raises_api_error_with_detail`: coverage for `_get` and `_patch`
  helpers.
- `test_fetch_file_inspect_raises_api_error_on_non_404`,
  `test_search_files_raises_api_error`: the two non-helper call sites that
  also route through `_raise_for_status`.
- `test_find_operator_by_username_returns_none_on_404`,
  `test_find_operator_by_username_reraises_non_404`: regression — `ApiError`
  is still catch-compatible with the existing `except httpx.HTTPStatusError`
  branch in `find_operator_by_username`.

### Unit — bot summary DM (`tests/test_bot_gateway_kb_command.py`)
- `test_kb_api_error_with_detail_is_surfaced_in_dm`: fake
  `submit_operator_upload` raises an `ApiError(detail="empty_text", ...)`; the
  operator DM contains the Russian friendly string ("извлечь текст") and does
  NOT contain `"Client error '422"`.
- `test_kb_inline_api_error_with_detail_is_surfaced`: same shape for the
  inline-text path (`api_failed:` prefix absent) — verifies that bare detail
  codes also hit `_API_DETAIL_FRIENDLY`.
- `test_kb_friendly_failure_reason_helper_covers_branches` extended with
  assertions for `empty_text`, `unsupported_source_file_type`,
  `missing_stored_binary_path`, `pdf_too_many_pages_for_ocr`.

### Integration / E2E
- No new e2e file. The existing `tests/e2e/test_e2e_epic09_*.py` flow remains
  representative because the bug fix is invisible at the happy-path level
  (text PDF, DOCX, PPTX, image, audio all stay on their existing extractor
  paths).

## Automated E2E verification
Story-aligned rows added to `_bmad-output/implementation-artifacts/e2e-coverage.md`.

## Manual Verification
1. `docker compose up --build -d`.
2. From the primary operator's Telegram account, send the original
   "Презентация для Реинфо.pdf" with caption `/kb_add`. The summary DM should
   read `✅ Добавлено в базу: 1 файл, N чанков, 0 помечен(о) confidential.`
   with `N > 0`, and `#<short_id> · Презентация для Реинфо.pdf` listed.
3. Force an `empty_text` 422 by sending a one-page blank PDF — the failure
   line in the summary DM should contain the Russian friendly reason
   (`API: из файла не удалось извлечь текст …`) and **not** the substring
   `Client error '422`.
4. Send a 60-page image-only PDF with the setting `OPERATOR_UPLOAD_PDF_OCR_MAX_PAGES=50` — the failure line should contain `PDF слишком длинный для распознавания`.
5. Confirm `find_operator_by_username` still returns `None` on 404 (e.g. send
   a message from an unregistered operator account — the bot should treat
   them as a customer, no traceback).

## Done Criteria
- `ruff check .` passes.
- `pytest --cov --cov-config=.coveragerc --cov-report=term-missing` passes with
  100% coverage on `platform_common/` and `services/`.
- New tests above all pass.
- All existing Epic 09 tests still green.
- Manual verification steps 2–4 produce the expected Russian DM strings.
