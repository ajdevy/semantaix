# Story 09.02 — Local extractors for text formats and image OCR

## Objective
Extract Russian text from PDF, DOCX, PPTX, TXT, and image files entirely locally — no paid API calls.

## Scope

### In Scope
- New package `services/api/app/operator_uploads/` with `extractors.py` exposing a dispatch table `EXTRACTORS: dict[str, Callable]` keyed by `source_file_type`:
  - `extract_pdf(path)` — `pypdf.PdfReader(path)`; concatenate `page.extract_text()` with `\n\n` between pages.
  - `extract_docx(path)` — `docx.Document(path)`; iterate `paragraphs` + table cell text.
  - `extract_pptx(path)` — `pptx.Presentation(path)`; per slide walk `shapes.text_frame` then `notes_slide.notes_text_frame`.
  - `extract_txt(path)` — `path.read_bytes().decode("utf-8", errors="replace")`.
  - `extract_image(path)` — `pytesseract.image_to_string(Image.open(path), lang="rus+eng")`.
- `soft_wrap(text, max_chars=200)` helper that sentence-segments via `razdel.sentenize` (already a dep); exposed as `RussianNormalizer.sentenize` for symmetry with `.lemmas`.
- Empty/whitespace-only extraction raises `ExtractionError("empty_text")`.

### Out of Scope
- Audio/video (story 09.03), API surface (09.04), bot orchestration (09.05).

## Implementation Notes
- New deps in `requirements.txt`: `pypdf`, `python-docx`, `python-pptx`, `Pillow`, `pytesseract`.
- Tesseract system packages installed in api Dockerfile in story 09.05.

## Test Plan

### Unit
- `tests/test_operator_uploads_extractors.py` with fixtures under `tests/fixtures/operator_uploads/`: a 1-page PDF generated in a fixture builder, minimal DOCX/PPTX/TXT, a small PNG. Image test patches `pytesseract.image_to_string` to return fixed Russian text. `soft_wrap` tested independently for 250-char inputs that need wrapping.

### Integration
None until 09.04.

## Automated E2E verification
Deferred to 09.05.

## Manual Verification
Run the extractor functions from a Python REPL inside the api container against the fixture files; confirm Russian text returns intact.

## Done Criteria
- All extractors return non-empty text on the corresponding fixture.
- 100% coverage on the new module.
- `ruff check .` passes.
