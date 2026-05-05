# BMAD Implementation Readiness Check

Date: 2026-05-05  
Scope checked:

- `PRD.md`
- `architecture.md`
- feature-sequential epic set under `epics/`
- Epic 01 story pack
- implementation gate policy

## Decision

**PASS (concerns resolved with explicit pre-implementation contracts)**

Project is ready to begin implementation under the strict one-epic-at-a-time model.

## What Passed

1. **Requirements-to-architecture alignment**
  - PRD and architecture are consistent on Option B, Docker-first, HITL behavior, guardrails, and backup/restore direction.
2. **Feature-sequential epic structure**
  - Epics are now feature-based and ordered.
  - Incident/alerts moved to Epic 02 as requested and enforced as a dependency for later epics.
3. **Execution gating**
  - Explicit gate policy exists and blocks parallel epic implementation.
4. **Epic 01 implementation readiness**
  - Story pack is detailed with scope, tests, and manual verification.
  - Out-of-scope constraints are explicit, reducing feature bleed.
5. **Backlog synchronization**
  - Linear issues are aligned to epic/story order and carry required verification context.

## Minor Concerns Resolution

1. **Epic 01 persistence schema boundary (locked)**
  - `conversations` (minimal): `id`, `telegram_user_id`, `created_at`, `updated_at`
  - `messages` (minimal): `id`, `conversation_id`, `source_message_id`, `role`, `text`, `trace_id`, `created_at`
  - Idempotency: unique constraint on `source_message_id` for duplicate webhook retries.
  - Deferred to later epics: escalation, incident lifecycle, moderation lifecycle, backup lifecycle tables.

2. **Guardrail contract deferred to Epic 03 (mitigated)**
  - Temporary Epic 01 response contract:
    - user-visible label: suggestion mode text in all AI responses
    - response flag: `is_suggestion_only=true`
    - telemetry fields: `response_mode=suggestion_only`, `guardrails_applied=false`
  - Transition note: replaced by guarded delivery behavior in Epic 03.

3. **Operational test data (standardized)**
  - Fixed Telegram update fixture set:
    - `update_message_text_basic.json`
    - `update_message_text_empty.json`
    - `update_duplicate_update_id.json`
    - `update_malformed_missing_core.json`
    - `update_callback_query_valid.json`
    - `update_edited_message_valid.json`
    - `update_non_text_message_photo.json`
  - Each fixture requires expected status + persistence + log outcome mapping.

## Required Rule for Start

- Only Epic 01 stories may be implemented now.
- Epic 02 cannot start until Epic 01 signoff checklist is complete.

## Next Action

Proceed to BMAD implementation phase initialization:

- Create sprint status artifact for this project
- Start **Epic 01 / Story 01.01** as the first implementation story

## Epic 08 Readiness Notes (Planning Add-On, 2026-05-05)

Epic **08 — Tenant Knowledge Operations, Answer Transparency, and Correction Loop** extends the roadmap **after** Epic 07. It is documented for planning traceability (**PRD FR-15–FR-17**, `architecture.md`) but is **not** part of initial MVP sequencing.

### New / Updated Dependencies

- **Hard**: Epic **05** (stable chunk ids + retrieval payload shape), Epic **03** (guardrail decision contract for traces), Epic **06** (optional moderation gate for NL ops and corrections), Epic **02** (incidents for trace/op/reindex failures).
- **Semantic**: MVP PRD §2 currently lists multi-tenant architecture as a non-goal; Epic 08 assumes **tenant isolation** for traces and knowledge. Product must either promote multi-tenant support to an explicit phased goal or scope Epic 08 to “single deployment / multiple logical tenants” with a documented isolation model before implementation starts.

### Risks

1. **NL mutation safety**: unparsed or malicious prompts could widen attack surface; require allowlists, strong confirmation UX, rate limits, and optional moderation-by-default for production tenants.
2. **Trace cost and retention**: payloads can grow quickly; enforce max chunk list size, truncation, retention policy, and PII alignment with structured logging rules.
3. **UX fragmentation**: bot-first NL ops versus Web UI parity—define minimum bar (bot complete, UI transparency required) before sprinting.
4. **Contract drift**: if Epic 05 chunk metadata or Epic 03 guardrail fields change without versioning, trace rendering breaks; add explicit DTO version field in trace JSON.

### Status

**Planning-only**: no implementation gate change for Epics 01–07; Epic 08 starts only after Epic 07 exit criteria and explicit signoff on tenant isolation approach.

