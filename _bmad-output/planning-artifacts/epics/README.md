# Semantaix Epics and Stories (Feature-Sequential)

This directory contains the BMAD feature-based sequential epic layout.

## Hard Rule
- Only one feature epic can be in implementation at a time.
- No feature from later epics may be implemented early.
- Next epic starts only after:
  - story tests pass
  - feature regression check passes
  - demo/acceptance signoff is completed

## Epic Order
1. `epic-01-telegram-llm-suggestions.md`
2. `epic-02-incident-alert-foundation.md`
3. `epic-03-guardrails-validity.md`
4. `epic-04-hitl-escalation.md`
5. `epic-05-rag-foundation.md`
6. `epic-06-knowledge-moderation.md`
7. `epic-07-backup-restore-hardening.md`
8. `epic-08-tenant-knowledge-ops-and-answer-traces.md`
9. `epic-09-operator-kb-growth.md`
10. `epic-10-multi-operator-projects.md`

## Recent Implementation Notes
- **Epic 04 (HITL escalation):** runtime HITL recipient/chat routing can be updated by Telegram command `/hitl_config @username <chat_id>`.
- **Access control:** only `HITL_CONFIG_ADMIN_USERNAME` (currently `@ajdevy`) is authorized to apply runtime HITL configuration changes.
- **Epic 09 (Operator KB growth):** the trusted HITL operator can grow the knowledge base from Telegram via slash command `/kb_add [confidential]` or Russian free-text intent (e.g. "добавь в базу", "сохрани в kb"). Supports PDF/DOCX/PPTX/TXT, image OCR (tesseract), and audio/video transcription (faster-whisper) — all local, zero external API spend. Uploads auto-publish (no second-human review); `confidential` uploads ground answers but redact `source_id` and `chunk_text` in answer-trace metadata.
- **Access control:** only the effective operator (runtime `hitl_primary_operator_username` or env default) can trigger `/kb_add`; non-operator messages are ignored with reason `unauthorized_kb`.

## Carry-forward Constraint
From Epic 03 onward, every epic must integrate with the incident/alerts solution from Epic 02.

## Automated E2E

Story-aligned pytest node ids (including Epic 07 backup/restore and Epic 08 traces/NL-ops/correction) live in **`_bmad-output/implementation-artifacts/e2e-coverage.md`**. CI runs **`pytest`** with coverage plus **`pytest -m e2e`**.
