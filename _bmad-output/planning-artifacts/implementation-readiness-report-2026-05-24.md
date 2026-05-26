# Implementation Readiness Assessment Report

**Date:** 2026-05-24
**Project:** Semantaix — Epic 13 (Unified Project Services Catalog)
**Scope:** PRD FR-23/24/25 + ext to FR-19/FR-20/FR-22 + §6/§10/§11 ↔ architecture (Epic 13 section, lines 222–265) ↔ epic-13 + 6 stories (13.01–13.06)

## Verdict: READY

The plan is coherent and traceable end-to-end. The PRD validation (2026-05-24) already resolved 22 findings (4 critical / 6 high / 7 medium / 5 low), and every one of those resolutions has a story owner with explicit acceptance + done-criteria coverage. The dependency graph is a sound DAG (13.01 is the universal foundation; 13.06 is parallel-after-13.01; 13.02→13.03 ∥ 13.04→13.05). Deferred items are consistent across PRD / architecture / epic / stories. No requirement is contradicted; no story invents scope absent from FR-23/24/25; Epic 11 calendar continuity is explicitly tested in story 13.06 and protected by the delegating-alias rule in 13.01. Phase 4 is green.

## Requirements → Story coverage

| Requirement | Covered by | Status |
|---|---|---|
| **FR-23** Canonical `project_services` table (rename + new columns, `UNIQUE(project_id, lower(name))`, idempotent + fresh-deploy migration, `ProjectServiceRepository` CRUD, per-key `asyncio.Lock`) | 13.01 (schema + repo + migration + lock + delegating aliases + `services_nl_op_sessions` bootstrap + `russian_calendar_terms.json`), 13.02 (REST CRUD + endpoint aliases), 13.06 (catalog-side consumer via `list_for_project`) | Full |
| **FR-24** Operator-facing service editing (slash + NL; permission split; preview threat model; full-payload audit; single-pending + ownership) | 13.02 (canonical REST CRUD + `authorize_service_remove` + endpoint aliases that both surfaces converge on), 13.03 (`/service add\|edit\|remove\|list` slash + `/calendar_service` alias + one-time DM hint + dedup table), 13.04 (`parse_service_intent` + `ServicesNlOpsRepository` state machine + 4 endpoints + single-pending + ownership + full-payload audit), 13.05 (bot dispatcher + plain-text preview + `parse_mode=None` + 200-char cap + prior-pending cancellation DM + operator-gating with no-DM-on-unauthorized) | Full |
| **FR-25** Catalog answer reads structured first + merge-with-dedup + humanistic rendering (no field labels; question-tailored; `source_id` literals; brownfield continuity) | 13.06 (catalog-branch cutover in `GroundedRagAnswerer` + `services_render.render_project_service_prose` + `services_catalog_merge.merge_structured_with_digest` + `grounding_system` rule migration + hedges audit + no-label-leak invariant test + single-row-doesn't-shrink test) | Full |
| **FR-22** (ext) Lemma-match against project-scoped calendar-eligible subset | 13.01 (`ProjectServiceRepository.list_calendar_eligible(project_id)` repo method shipped; filter `duration_minutes IS NOT NULL`); Epic 11 `service_resolver` consumer is unchanged code (calls the new repo seam via the delegating alias for one release, then directly) | Full |
| **FR-19 / FR-20** (ext) Calendar reads sourced from `project_services` calendar-eligible subset | 13.01 (repo provides `list_calendar_eligible`); Epic 11 `compute_availability` consumer is unchanged code (reads via the delegating `CalendarSettingsRepository.list_service_rules` alias for the deprecation window, then direct in Epic 14 cleanup) | Full |
| **§6 data-stores updates** (`project_services` in `semantaix_calendar.db`; `services_nl_op_sessions` in `semantaix_nl_ops.db`; `answer_traces.source_id` literals `project_services:<id>` / `catalog_digest:<id>` / `merged:<id>`) | 13.01 owns both new stores + the bootstrap; 13.06 owns the three `source_id` literal writes in the catalog branch | Full |
| **§11 Glossary updates** (Project service; Schedulable service) | Doc-only edits already applied in PRD §11 lines 543–544; no story needed | Full (doc) |

Summary: **7 requirement rows; 7 fully covered (no warnings, no gaps).**

## Validation findings — resolution status

All 22 findings from the 2026-05-24 validation pass have explicit story owners reflected in In-Scope + Acceptance + Done Criteria. None are orphans.

### Critical (4 of 4 covered)
- **C1** permission split for `/service remove` — owned by **13.02** (`authorize_service_remove` helper + endpoint 403) and **13.03** (bot relays 403 as Russian operator-only message); confirm-time re-check also in **13.04**.
- **C2** uniqueness + upsert key on `(project_id, lower(name))` + lemma resolution against project-scoped subset — owned by **13.01** (schema constraint + `upsert` key + `acquire_service_upsert_lock` keyed identically + `list_calendar_eligible` filter).
- **C3** binary-cliff regression (merge with dedup; single-row doesn't shrink digest) — owned by **13.06** (`services_catalog_merge.merge_structured_with_digest` + explicit "single-row + 12-service digest → 12 names" contract test).
- **C4** NL threat model (plain-text preview, ownership-verified confirm, single pending per `(project, operator)`) — split across **13.04** (api: ownership re-check, atomic prior-pending cancellation, soft-delete with full-payload audit) and **13.05** (bot: plain-text DMs with `parse_mode=None` asserted, 200-char cap, prior-pending cancellation DM, no-DM on unauthorized).

### High (6 of 6 covered)
- **H1** migration idempotency via existence-check guards + fresh-deploy path — **13.01** (explicit "run twice" + "fresh deploy without Epic 11" + "touch isolation" migration tests).
- **H2** deprecation window pinned to Epic 14 cleanup ≤60 days + user-facing DM hint — **13.01** (repo aliases + log only), **13.02** (endpoint aliases + log only), **13.03** (`/calendar_service` alias + one-time DM hint + persistent dedup row).
- **H3** per-`(project_id, lower(name))` `asyncio.Lock`, last-writer-wins — **13.01** (`acquire_service_upsert_lock` helper + lock-equality test), consumed by **13.02** (REST upsert) and **13.04** (NL confirm upsert) so slash + NL converge under one lock.
- **H4** label-leak elimination + hedges audit — **13.06** (`services_render` strips labels at the repository boundary + explicit "no label leak" assertion across all 3 fixture paths + `tests/test_russian_hedges_audit.py`).
- **H5** full-payload audit logging for service content — **13.04** (`services_nl_op_confirmed` / `_cancelled` / `_expired` events log full payload with explicit log-capture test asserting `price_text` + `description` present); NFR-3 OAuth-redaction scope explicitly unchanged.
- **H6** JSON shapes pinned + Russian rendering map — **13.01** (schema + JSON shape unit tests + `data/russian_calendar_terms.json` file created), **13.06** (renderer consumes the map; multi-window + date-exception rendering tests).

### Medium (7 of 7 covered)
- **M1** NL regex coverage (must-parse + must-fail-closed examples) — **13.04** (`tests/test_parse_service_intent.py` covers all FR-24 examples + Ё/Е + dash variants).
- **M2** no-data-dump bound (general → names only; single-service → at most one price + one description) — **13.06** (contract tests assert both bounds against multi-row fixtures).
- **M3** `source_id` literal pinned — **13.06** (writes `project_services:<id>` / `catalog_digest:<id>` / `merged:<id>` literally; all 4 branches contract-tested).
- **M4** trigger anchoring + no-DM on unauthorized — **13.03** (slash regex `^\s*/service\b…` + negative tests for mid-message + quoted-reply), **13.04** (api parser anchored for defense in depth), **13.05** (NL regex `^\s*(добавь\|…)\s+услугу\b` + negative tests; non-registered sender → zero `send_message` calls asserted by capture test).
- **M5** soft-delete + 30-day retention + lazy expiry reap — **13.04** (state machine retains payload through soft-delete; `latest_pending` reaps lazily; `purge_soft_deleted_older_than` helper shipped).
- **M6** migration touch-scope (only the rename + 4 new columns) — **13.01** (snapshot test asserts `calendar_project_settings` / `calendar_operator_tokens` / `calendar_oauth_pending_state` schemas + row counts unchanged before/after).
- **M7** FR-22 stale wording aligned to "schedulable" subset — already applied in PRD §FR-22 (lines 321–323); **13.01** ships the `list_calendar_eligible` repo method that operationalizes it.

### Low (5 of 5 covered)
- **L1** §10 omission — already fixed in PRD §10 (line 535 references Epic 13).
- **L2** wrong-DB placement for sessions table — fixed in PRD §6 and reflected in **13.01** (`services_nl_op_sessions` created in `semantaix_nl_ops.db`, not `semantaix_calendar.db`).
- **L3** overconfident "no conflicts" framing — fixed in decision-log Epic 13 entry (three near-conflicts now explicit).
- **L4** glossary eligibility split — fixed in PRD §11 (line 543).
- **L5** admin-gate narrowing not flagged — fixed in decision-log Epic 13 entry + reflected in **13.02** + **13.04** (admin-must-also-be-registered-project-operator).

**Summary: 22 of 22 findings have story owners; 0 orphans.**

## Alignment checks

### Dependency order
Sound DAG: `13.01 → {13.02 → 13.03 ∥ 13.04 → 13.05} ∥ 13.06`. No cycles. 13.01 correctly gates everything (schema + repo + `services_nl_op_sessions` table + `russian_calendar_terms.json` data file + `acquire_service_upsert_lock` helper are all consumed by later stories). 13.06 explicitly depends ONLY on 13.01 (consumes the repo + data file; independent of the NL branch). 13.03 depends on 13.02 (slash command rides the canonical REST endpoint); 13.04 depends on 13.01 only (`ServicesNlOpsRepository` populates the table 13.01 created; uses the lock helper from 13.01) — the README graph shows 13.04 as a child of 13.02, but 13.04's actual code dependency is only on 13.01's repo + lock + table; the README ordering is a logical "13.02 should land first because both expose endpoints that share the auth seam" preference, not a hard dependency. This is consistent and not a blocker. 13.05 depends on 13.04 (consumes the 4 NL ops endpoints). All claimed dependencies are present in the cited stories.

### Deferred items consistency
Checked across PRD / architecture / epic / 6 stories:
- **LLM PDF extraction** — deferred consistently in PRD (FR-23/24 deferral notes), architecture (line 265), epic Out-of-Scope, stories 13.04 + 13.06 Out-of-Scope.
- **Web admin UI** — deferred consistently in PRD (FR-24 implication), architecture (line 265), epic Out-of-Scope, stories 13.02 + 13.05 Out-of-Scope.
- **Multi-operator / multi-calendar** — deferred consistently as "Epic 11 deferrals carry forward unchanged" in PRD, architecture, epic, decision-log.
- **Optimistic concurrency (`updated_at` precondition)** — deferred consistently with "last-writer-wins is acceptable for v1" framing in PRD (FR-23 §Concurrency line 360), architecture (line 256), epic, story 13.01.
- **Booking / event creation** — deferred consistently (Epic 11 §2.2 Non-Goal carried forward) in PRD, architecture, epic.
- **Alias removal** — pinned to "Epic 14 cleanup PR (≤60 days post-merge)" consistently in PRD (FR-23 line 361; FR-24 line 375), architecture (line 260), epic Out-of-Scope, stories 13.01 (repo aliases) + 13.02 (endpoint aliases) + 13.03 (`/calendar_service` alias).

No drift.

### No orphan requirements
Every FR-23 / FR-24 / FR-25 acceptance criterion maps to ≥1 story. Spot-checked the load-bearing ones:
- FR-23 "running twice is a no-op" + "fresh deploy without Epic 11 migration" → 13.01 explicit tests.
- FR-23 "other tables in `semantaix_calendar.db` untouched" → 13.01 snapshot test.
- FR-24 "non-registered sender's `/service` or `добавь услугу …` triggers nothing (no DM)" → 13.03 + 13.05 explicit no-DM tests.
- FR-24 "admin-who-is-registered-operator can add/edit but `/service remove` → 403" → 13.02 + 13.03 + 13.04 (confirm-time re-check) cover all three surfaces.
- FR-24 "second `добавь услугу …` cancels prior + DMs" → 13.04 atomic-cancel-of-prior test + 13.05 cancellation-DM-before-new-preview test.
- FR-24 "confirm verifies originating-operator" → 13.04 repo-level + endpoint-level tests + 13.05 cross-operator-replay capture.
- FR-24 "must parse" + "must fail closed" examples → 13.04 parser tests cover each example by name.
- FR-25 "no label leak" → 13.06 invariant test across structured / digest / merged paths.
- FR-25 "single-row doesn't shrink 12-service digest" → 13.06 explicit contract test.
- FR-25 "source_id literal" for all 4 branches → 13.06 contract test.
- FR-25 "brownfield continuity (digest-only project)" → 13.06 e2e case (a).

No story invents scope absent from PRD/architecture; In-Scope sections cite FR / architecture line refs throughout.

### No contradictions with Epic 11
- **`service_resolver` calendar path preserved** — 13.06's merge-with-dedup operates only in `GroundedRagAnswerer`'s catalog branch; the calendar `service_resolver` reads via 13.01's `list_calendar_eligible` (the `duration_minutes IS NOT NULL` predicate). Epic 11 service-resolver code path is structurally untouched.
- **Epic 11 calendar tests preserved** — 13.01 ships `CalendarSettingsRepository.upsert_service_rule` / `list_service_rules` / `delete_service_rule` as delegating aliases that call through to `ProjectServiceRepository`. Existing Epic 11 tests that exercise the old method names continue to pass; they just emit a `deprecation_warning_calendar_settings_service_rule` log alongside the existing assertions.
- **Operator-only destructive-op rule (FR-18 / FR-21) carried forward** — `/service remove` is operator-only with admin → 403 `admin_cannot_remove_service`, identically structured to FR-18's `/disconnect_calendar` admin → 403 `admin_cannot_disconnect`. Add/edit are shared with admin-who-is-also-registered-project-operator (narrower than FR-21's plain admin gate; called out in decision-log L5 and reflected in 13.02 + 13.04).
- **Calendar enable/disable surface untouched** — Epic 13 makes no changes to `/connect_calendar` auto-enable, `/calendar_off`, or `POST /disable`. The 2026-05-24 Epic 11 follow-up (drop `/calendar_on`) is preserved.

### Project-context compliance
Every story respects the binding rules:
- **Sync sqlite3 + `asyncio.to_thread`** — 13.01 (`ProjectServiceRepository` is sync sqlite3; lock + dispatch live in the caller), 13.04 (`ServicesNlOpsRepository` is sync sqlite3 mirroring `admin_nl_ops`), 13.02 / 13.04 explicit `to_thread(repo.upsert)` wrapping.
- **Injected clock + httpx client** — 13.04 takes `now` in `create_pending` / `consume` / `cancel` / `latest_pending`; the bot ApiClient additions in 13.05 ride the existing injected `httpx.AsyncClient` in `services/bot_gateway/app/api_client.py`.
- **Per-layer failure boundaries** — 13.02 raises `HTTPException` at the endpoint (400 / 403 / 404 / 410); 13.04 raises typed domain errors (`NlOpSessionNotPending`, `NlOpSessionExpired`, `NlOpSessionNotOwner`, `NlOpInvalidToken`) at the repo and translates them to HTTPException at the endpoint; 13.06 catalog answerer either renders or falls through to existing `_skip(reason='catalog_empty')` — no answerer-internal exceptions leak into the pipeline.
- **Structured logging with `trace_id`** — every story specifies log event names (`services_upsert_duplicate_name`, `deprecation_warning_calendar_settings_service_rule`, `deprecation_warning_calendar_services_endpoint`, `deprecation_warning_calendar_service_command`, `services_nl_op_confirmed/_cancelled/_expired`, `unauthorized_services`); 13.04 explicitly includes `trace_id` in the full-payload audit log.
- **Never log secrets** — explicitly preserved (NFR-3 OAuth-redaction scope unchanged); operator-published service content is non-secret and IS logged (full payload), with an explicit defensive log-capture test in 13.04 asserting `price_text` is present (catches a future cargo-cult redaction mistake).
- **Ruff line-length 100** — every story's Done Criteria includes `ruff check .` passes.
- **100% coverage** — every story's Done Criteria includes a 100%-on-new-modules clause with explicit file lists.

No story is silent on a relevant rule.

## Findings

### High
None.

### Medium
None.

### Low (polish items; do not block Phase 4)
- **L-PR-1 (doc polish).** The README dependency graph (`stories/epic-13/README.md` lines 17–22) shows 13.04 as a child of 13.02. The actual code dependency is 13.04 → 13.01 only (13.04 uses `ServicesNlOpsRepository` against the table 13.01 created + the lock helper from 13.01; it does NOT call any 13.02 endpoint). The "13.02 before 13.04" ordering is a logical "land the auth seam first" preference, not a hard dependency. Worth noting in the sprint plan so 13.04 + 13.02 can in principle ship in parallel if developer capacity allows. No code/spec change needed.
- **L-PR-2 (doc polish).** Story 13.03's `/service edit` flow does a client-side `GET /api/projects/{id}/services` + lookup for the "name not found" pre-check (line 13). Could be slightly faster as a `GET /api/projects/{id}/services/{name}` shortcut, but the story explicitly defers that to keep the api surface minimal. Track as a possible follow-up if observed latency becomes a problem; not a blocker.
- **L-PR-3 (track during dev).** The `calendar_service_alias_hint_sent` dedup table in 13.03 is created lazily on first `/calendar_service` hit (not in the api startup bootstrap). This is intentional (the table is purely a behavior-attached dedup, not a session-state table), but worth a one-line comment in the migration file pointing to where the `IF NOT EXISTS` bootstrap lives so future readers don't grep for it in `semantaix_nl_ops.db` bootstrap and miss it.
- **L-PR-4 (track during dev).** Story 13.06's day-grouping stretch goal (`пн–сб 10:00–19:00` when mon..sat share the same window) is correctly marked as optional with per-day fallback. If grouping ships, the renderer test fixture should pin the form so future refactors don't silently flip output shape.

## Gate
Green for Phase 4 (`bmad-sprint-planning` → 13.01). Epic 13 is a 6-story pack with all 22 validation findings resolved + owned, sound dependency DAG, consistent deferrals across PRD/architecture/epic/stories, and no Epic-11 contradictions. The four Low items above are polish/tracking, not gates.

## Notes
- One-PR-per-epic shape is preserved (6 commits inside one PR, story-by-story, each with 100% coverage gate) — matches the Epic 13 plan and is consistent with the project-context "One PR per story" rule's intent (small reviewable units mapping 1:1 to the BMAD cycle).
- `scripts/epic13_signoff.sh` + `_bmad-output/implementation-artifacts/e2e-coverage.md` rows for 13.01–13.06 are referenced by the epic file and stories; ensure these land alongside story 13.01 (the signoff script) and as the story PRs land (the coverage matrix rows).
- The hedges audit (story 13.06) is a release-readiness assertion — if a future content change adds price-like phrasings to `data/russian_hedges.txt`, the audit test will fail loudly, which is the desired behavior.
