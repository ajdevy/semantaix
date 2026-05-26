# Validation Report — Semantaix PRD (Epic 13: Unified Project Services Catalog)

- **PRD:** `_bmad-output/planning-artifacts/PRD.md`
- **Rubric:** `.claude/skills/bmad-prd/assets/prd-validation-checklist.md`
- **Run at:** 2026-05-24
- **Grade:** Fair (PRD body strong; calendar FRs strong; new Epic 13 surface had 4 critical + 6 high + 7 medium + 5 low findings, all RESOLVED in this pass)
- **Reviewers:** rubric-walker, adversarial-general

## Overall verdict

The PRD body and the Epic 11 calendar feature group (FR-18–FR-22) remain decision-ready and unchanged in posture from the 2026-05-22 validation. The Russian-first, escalate-on-uncertainty safety thesis still holds; secret-handling and OAuth boundaries continue to be the only piece of cross-feature crypto prose; the bootstrapped decision log is clean.

The new Epic 13 surface (FR-23 / FR-24 / FR-25, plus §6 and §11 deltas) lands as a tight, surgical unification — one canonical `project_services` table powering both the catalog answer and the calendar, with two converging operator entry paths (slash + NL). Its decision-readiness and strategic coherence are strong. Where it ran thin was **done-ness clarity** (service identity, NL session lifecycle, migration idempotency, and audit-discipline boundaries were under-specified) and **downstream usability** (the kickoff "no conflicts" claim walked past three real near-conflicts with Epic 11).

The validation pass produced 22 findings across both reviewers — 4 critical, 6 high, 7 medium, 5 low — and **all 22 are resolved** in this pass via direct edits to FR-22 / FR-23 / FR-24 / FR-25 / §6 / §10 / §11 plus a validation-resolution sub-section in `.decision-log.md`. Highlights: per-`(project_id, lower(name))` uniqueness + locking (C2/H3); FR-25 now **merges** structured + digest with dedup rather than the original binary cliff (C3); NL preview hardened against MarkdownV2 injection and cross-operator replay (C4); operator-only `/service remove` mirroring FR-18's destructive=operator-only rule (C1); audit now logs the full payload (H5); migration is genuinely idempotent via existence-check guards (H1). Verdict after resolution: **READY for architecture.**

## Dimension verdicts (rubric-walker)
- Decision-readiness — strong
- Substance over theater — strong
- Strategic coherence — strong
- Done-ness clarity — thin → **adequate after resolution**
- Scope honesty — adequate → **strong after resolution** (deferrals now surface in PRD body, not just decision log)
- Downstream usability — adequate → **strong after resolution** (§10 now mentions Epic 13; §6 pins source-id literals; sessions DB normalized)
- Shape fit — strong

## Findings by severity

### Critical (4) — RESOLVED

- **C1 — Permission model silently contradicts Epic 11's "destructive = operator-only" split** (FR-24, cross-ref FR-18/FR-21). *Resolution:* `/service add` and `/service edit` are operator-AND-admin (non-destructive, like enable/disable); `/service remove` is operator-only (destructive, like disconnect) — admin attempting `remove` returns 403 `admin_cannot_remove_service`. Admin must also be a registered project operator (narrower than FR-21).
- **C2 — No per-project name uniqueness; upsert key undefined** (FR-23, FR-24, FR-22, FR-25). *Resolution:* `UNIQUE(project_id, lower(name))` declared; `ProjectServiceRepository.upsert` keyed on `(project_id, lower(name))`; duplicate-name attempts update existing row and emit `services_upsert_duplicate_name`; FR-22 lemma resolution scoped to project's calendar-eligible subset.
- **C3 — FR-25 "fall back only if empty" is a binary cliff that silently regresses partially-migrated projects** (FR-25). *Resolution:* FR-25 now **merges** structured rows AND digest content with deduplication (lemma-match of structured name against digest tokens; structured row wins on conflict). Single-row insert no longer shrinks a 12-service digest to 1. Trace `source_id` `merged:<project_id>` covers the merged branch.
- **C4 — NL dialog threat model under-specified** (FR-24). *Resolution:* preview DM rendered as plain text (no MarkdownV2/HTML) with operator content escaped + 200-char cap; confirm endpoint verifies `session.originating_operator == current_sender` (cross-operator replay → 403 `not_session_owner`); at most ONE pending session per `(project_id, operator)` — second trigger cancels prior with operator DM.

### High (6) — RESOLVED

- **H1 — Migration "idempotent ALTER" claim is technically wrong** (FR-23). *Resolution:* genuinely idempotent via existence-check guards (`sqlite_master` for table presence, `PRAGMA table_info` for columns). Fresh-deploy path CREATEs `project_services` directly when neither old nor new table exists. Acceptance: "running twice is a no-op" + "fresh deploy without Epic 11 migrations succeeds".
- **H2 — "One release" deprecation window undefined; immediately-prior precedent was zero-period** (FR-23, FR-24). *Resolution:* aliases (`CalendarSettingsRepository.upsert_service_rule`, `/calendar_service`, calendar service REST routes) removed in **Epic 14 cleanup PR, no later than 60 days post-Epic-13-merge**; deprecated paths log AND DM the operator a user-facing migration hint.
- **H3 — Concurrent edits race / lost-update unspecified** (FR-23). *Resolution:* per-`(project_id, lower(name))` `asyncio.Lock` around `ProjectServiceRepository.upsert` (single-flight, mirroring Epic 11 token-refresh lock). Last-writer-wins; no `updated_at` precondition checks in v1.
- **H4 — FR-25 structured chunk leaks field labels into LLM input** (FR-25). *Resolution:* rendering moved to repository boundary as natural Russian prose ("Маникюр — 60 минут, пн–сб 10:00–19:00, цена от 2000 ₽."), no field-label tokens. Acceptance: answer contains no `Название:` / `Цена:` substrings. `data/russian_hedges.txt` audited against price/duration phrasings before merge.
- **H5 — Audit "keys not values" destroys the audit's purpose** (FR-24, §6). *Resolution:* `services_nl_op_confirmed` / `*_cancelled` / `*_expired` carry FULL payload (name, description, price_text, tags, scheduling fields). Operator-published content is non-secret. Same posture as today's `answer_traces`. NFR-3's OAuth-redaction scope unchanged.
- **H6 — JSON shapes + Russian rendering unspecified** (FR-23, FR-25). *Resolution:* `working_hours_json` `{"mon":[["10:00","19:00"]]}`, `service_days_json` `["mon",…]`, `date_exceptions_json` `["2026-01-01",…]` pinned in FR-23. New data file `data/russian_calendar_terms.json` for Russian rendering; multi-window-per-day → `"пн 10:00–13:00, 14:00–19:00"`; date exceptions → `"закрыто: 1 января, 9 мая"`.

### Medium (7) — RESOLVED

- **M1 — NL regex "at minimum" coverage leaves real utterances undefined** (FR-24). *Resolution:* explicit "must parse" examples (six-field full sentence, two-token name, three Cyrillic dash variants, "ё"/"е" normalization for free via `RussianNormalizer`) and "must fail closed" examples (two-services-in-one, non-digit duration) added to FR-24 acceptance.
- **M2 — FR-25 "no full data dump" lacks a measurable bound** (FR-25). *Resolution:* AC #2 replaced with concrete bound — single-service question → at most one `price_text` and at most one `description`; general question → names only, no prices/descriptions unless explicitly asked.
- **M3 — FR-25 source-id literals not pinned in §6** (FR-25, §6). *Resolution:* §6 row for `semantaix_answer_traces.db` now lists the three literals `project_services:<id>` / `catalog_digest:<id>` / `merged:<id>` with their meanings.
- **M4 — Trigger keywords collide with customer Russian + sender-routing edge case** (FR-24). *Resolution:* triggers anchored to start-of-message (regex `^\s*(добавь|добавьте|новая|создай|удали|измени)\s+услугу\b`); non-registered senders ignored with **no DM** (logged `unauthorized_services`) to avoid customer-thread "trigger matched but reply went to DM" weirdness.
- **M5 — `payload_json` "never logged" means audit is unreconstructible** (§6, FR-24). *Resolution:* superseded by H5 (full payload logged at confirm). Additionally `services_nl_op_sessions` rows are soft-deleted on confirm/cancel/expire (30-day audit retention); expired sessions reaped lazily.
- **M6 — Migration scope to other tables in `semantaix_calendar.db` unstated** (FR-23). *Resolution:* migration touches ONLY the rename and four new columns. `calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state` are unchanged (verified by schema snapshot in the acceptance test).
- **M7 — FR-22 wording stale w.r.t. FR-23's eligibility split** (FR-22). *Resolution:* FR-22 now says "configured **schedulable** service" with explicit `duration_minutes IS NOT NULL` predicate; catalog-only rows are intentionally invisible to the calendar resolver.

### Low (5) — RESOLVED

- **L1 — §10 Delivery Mapping omits Epic 13** (§10). *Resolution:* Epic 13 paragraph added — references Epic 11/10/09 dependencies and the epic file to be created by `bmad-create-epics-and-stories`.
- **L2 — `services_nl_op_sessions` placed in `semantaix_calendar.db` instead of `semantaix_nl_ops.db`** (§6). *Resolution:* moved to `semantaix_nl_ops.db` alongside `nl_op_sessions` and `admin_nl_op_sessions`. Operator runbook "show all pending NL sessions" now queries a single DB.
- **L3 — Decision-log "Conflicts surfaced — (none)" overconfident** (`.decision-log.md` Epic 13 entry). *Resolution:* sub-section rewritten to list the three real near-conflicts (C1 permission asymmetry, H5 audit-discipline cargo-cult, H2 deprecation cadence vs Epic 11 zero-period precedent) and how each is resolved.
- **L4 — Glossary "Project service" doesn't surface eligibility split** (§11). *Resolution:* entry now states "catalog-eligible always; calendar-eligible iff `duration_minutes IS NOT NULL`" with FR-23 / FR-20 / FR-22 cross-references.
- **L5 — Decision log doesn't flag admin-gate narrowing** (`.decision-log.md`). *Resolution:* bullet added to Epic 13 entry noting FR-24's admin must also be a registered project operator (narrower than FR-21's plain admin gate); rationale: services are project-content, not platform-level config.

## Mechanical notes
- **ID continuity:** FR-1–FR-25 contiguous and unique; NFR-1–NFR-7 contiguous; §1–§11 in order. No conflict markers.
- **Decision-log alignment:** the Epic 13 kickoff entry is preserved; the validation-resolution sub-section is appended (not overwritten). The Conflicts-surfaced sub-section is rewritten to drop the overconfident "(none)" claim.
- **Glossary:** unchanged shape; "Project service" entry tightened with the eligibility predicate.
- **Forward ref:** `epic-13-unified-project-services-catalog.md` (§10) not yet created (expected — Phase 4).

## Reviewer files
- `review-rubric-epic13.md`
- `review-adversarial-epic13.md`
