# Epic 09: Operator-Driven KB Growth via Telegram

## Goal
Let the trusted HITL operator grow the knowledge base by sending text, files, images, audio, or video to the Telegram bot — with optional confidentiality — at zero external API cost. Operator uploads auto-publish (no second-human review) and immediately ground future customer answers via Epic 05 retrieval.

## In Scope
- Operator-only command `/kb_add [confidential]` plus Russian free-text intent ("добавь в базу", "сохрани в kb", "запомни в базу знаний", …) recognized in message text or attachment caption.
- Telegram-side download of `document` / `photo` / `voice` / `audio` / `video` attachments via the bot file API.
- Local extraction: PDF (`pypdf`), DOCX (`python-docx`), PPTX (`python-pptx`), TXT, image OCR (`tesseract-ocr-rus`), audio/video transcription (`faster-whisper` `base` int8) with a duration cap and binary-SHA256 dedup.
- Auto-approved knowledge candidates (`status='approved'`) with confidentiality flag, source file metadata, and stored binary path persisted on a private volume.
- RAG ingest with per-chunk `is_confidential` flag.
- Audit-metadata redaction in `GroundedRagAnswerer` so confidential chunks don't echo `source_id` / `chunk_text` outside the LLM grounding boundary.
- Incident emission via Epic 02 backbone for extraction failures.

## Out of Scope
- Moderation queue / second-human review for operator uploads (auto-publish by design).
- LLM-based intent detection (keyword/regex only).
- Vision-LLM OCR (tesseract handles images locally).
- Cloud transcription APIs (OpenAI Whisper, AssemblyAI, …) — local only.
- Media-group batching (each Telegram update is processed independently).
- Editing previously uploaded knowledge (deletion is in scope via Story 09.07).
- Multi-operator support — single trusted operator only.

## Dependencies
- **Epic 04** — HITL operator identity (`hitl_primary_operator_username`) and `/hitl_config` runtime override pattern.
- **Epic 05** — RAG ingest / retrieve and chunk schema.
- **Epic 06** — Knowledge moderation schema and approval lifecycle.
- **Epic 02** — Incidents/alerts backbone for extraction-failure surfacing.

## Exit Criteria
- Operator can issue `/kb_add` (or Russian free-text intent) with attachments in a single Telegram message and see the bot ack within 1 s plus a summary DM after extraction.
- Each `source_file_type` extractor produces text that flows through the existing `RagRepository.ingest` path.
- Confidential uploads ground customer answers correctly while `metadata.retrieval` in the answer trace is redacted (`source_id == "knowledge_candidate:confidential"`, `chunk_text == "[redacted]"`).
- Identical re-upload of a previously processed file short-circuits to zero extraction work and zero new chunks.
- Failures (oversize file, empty extraction, ffmpeg crash, whisper crash) surface as Epic 02 incidents and bot DM error messages in Russian.
- Zero external API spend introduced.

## Automated E2E verification
- Story-aligned tests under `tests/e2e/test_e2e_epic09_*.py` (`@pytest.mark.e2e`).
- New scripted signoff: `scripts/epic09_signoff.sh` (local operator helper).
- Matrix updated in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
