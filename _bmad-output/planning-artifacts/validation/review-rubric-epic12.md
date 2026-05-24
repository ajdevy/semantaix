# PRD Quality Review — Semantaix PRD (Epic 12: Unified Project Services Catalog)

## Overall verdict

Epic 12 lands as a tight, surgical addition: it states a real problem (two disjoint "services" concepts), names a concrete unification (rename `calendar_service_rules` → `project_services`, calendar-eligibility = `duration_minutes IS NOT NULL`), and converges two operator entry paths (slash + NL) on one repository. FR-23/FR-24/FR-25 are decision-shaped, traceable to the decision log, and largely consistent with the Epic 11 decisions they extend. The main risks are **done-ness gaps around service identity** — name uniqueness, NL "edit/remove" target resolution, and migration idempotency are under-specified — and a couple of latent conflicts with prior permission framing that the log doesn't flag.

## Decision-readiness — strong

The new content reads like decisions, not balancing acts. The decision log entry at the end of `.decision-log.md` ("Epic 12 — Unified Project Services Catalog (planning kickoff, 2026-05-24)") lists nine "Decisions locked" — each is a stated choice with a rationale, not a "consideration." The "Conflicts surfaced" section explicitly searched prior decisions and walked through why the unification doesn't break the OAuth/secrets rule (NFR-3 / FR-18) — exactly the kind of confront-the-objection move the rubric rewards. The "Explicitly deferred" block names what's out (LLM PDF extraction, web admin CRUD, multi-operator/multi-calendar) without smoothing over them.

FR-23's calendar-eligibility predicate (`duration_minutes IS NOT NULL`) is a decision posed cleanly enough that an engineer can implement it without reinterpretation. FR-24's "regex only in v1, LLM extraction is a future epic" is a real trade-off named with what was given up (fuzzy NL coverage). FR-25's fallback ordering (`project_services` → `catalog_digest` → `_skip`) is three branches in priority order with a deterministic acceptance test for the last branch.

### Findings
- **low** Deprecation alias has no sunset criterion (FR-23 §4, FR-24 Path A) — Both `CalendarSettingsRepository`'s service-rule method aliases and the `/calendar_service` slash alias "remain for one release as a deprecation-logged alias", but "one release" is undefined for this project (no release cadence is named anywhere in the PRD). *Fix:* either define "one release" operationally (e.g. "removed in Epic 13 cleanup" or "removed after 30 days in production") or accept it as `[ASSUMPTION]`.

## Substance over theater — strong

No furniture in the new content. FR-23/FR-24/FR-25 each carry behavior that a reader can object to. The Glossary additions (§11 "Project service", "Schedulable service") are load-bearing — they actually disambiguate the "service" overload that the original PRD already flagged for Epic 11 and that Epic 12 makes worse without them. The "Avoid the word 'bookable'" parenthetical in the Schedulable service entry shows real glossary hygiene.

The Feature Group preamble ("This eliminates the prior duplication where the same offering ('маникюр') had to be described once as a calendar service rule and again indirectly via an uploaded PDF.") is the kind of one-sentence problem statement that earns its place — it names the duplication being removed, not a vague "improves UX."

No findings.

## Strategic coherence — strong

The Epic 12 thesis is explicit: one canonical, operator-curated services table powers both the catalog answer and the calendar; everything else (NL dialog, audit-keys-only, structured-first-then-digest fallback) follows from that thesis. Feature prioritization tracks the thesis — the catalog read path (FR-25) depends on the canonical table (FR-23), and the editing surface (FR-24) is what makes the table reach operator-curated state without engineering touch. No "list of capabilities someone wanted."

The decision log's "Problem being resolved" paragraph is the kind of framing the rubric calls for — it names the two concrete artifacts being unified (`catalog_digests`/`catalog_digest.py` on one side, `calendar_service_rules` on the other) rather than waving at "fragmentation." Success metrics weren't added for Epic 12, which is the correct call for an internal refactor-plus-surface: it does not need a tracked deflection metric — the existing Epic 11 availability-answer metric and the existing catalog-empty trace branch already cover observable outcomes.

No findings.

## Done-ness clarity — thin

This is the dimension where Epic 12 most needs more work. Most FR-23/FR-24/FR-25 acceptance criteria are testable, but several behaviorally-important branches are silent.

**FR-23.** "The rename is an idempotent ALTER" (line 341) but SQLite `ALTER TABLE … RENAME TO` is not idempotent and not transactional with adding new columns. With the empty production table, the practical risk is zero, but the acceptance criteria do not specify how the migration handles a re-run (already renamed? new columns already added? mixed state from a partial failure?). The acceptance reads "After migration, `calendar_service_rules` no longer exists; `project_services` has all listed columns" — that's a post-condition, not an idempotency guarantee.

**Service identity / uniqueness is unspecified.** FR-23 lists `name (REQUIRED)` but no per-project uniqueness constraint. FR-24 talks about `ProjectServiceRepository.upsert` — but the upsert key is never named. Two operators could create two services both named "маникюр" with different durations; FR-25's tailored-answer criterion ("'Сколько стоит маникюр?' → the answer includes the `price_text` for 'маникюр' specifically") becomes ambiguous. The calendar resolver (FR-22 lemma matching) would also see a duplicate match. Equally, FR-24's `/service remove <name>` and `удали услугу <name>` have no defined behavior when the name resolves to multiple rows.

**NL dialog regex acceptance is a floor, not a ceiling.** FR-24 says "Russian regex extracts at minimum: name; optional duration… optional days… optional hours… optional price… optional desc." The "at minimum" framing leaves edge cases unaddressed: Cyrillic dash variants (en-dash, em-dash, hyphen) in "пн–сб" vs "пн-сб"; mixed Latin/Cyrillic in "10-19" vs "10—19"; "ё" vs "е"; multi-word service names ("маникюр гель-лак"); how to delimit name end vs description start when `описание:` is absent. The "ambiguous fails closed" rule covers safety but not coverage.

**NL session lifecycle behavior partially specified.** TTL is 600s, status enum is listed, `confirm_token` is single-use — good. But there's no acceptance for: what happens to expired sessions (garbage collected? left in the table? blocked from create-new-while-pending?); whether the operator can issue a second `добавь услугу …` while a prior session is still `pending_confirmation` for the same project; whether the bot DMs anything on TTL expiry.

**FR-25 humanistic-rendering rule is judgment-shaped.** The Russian guidance string in FR-25 ("Если клиент просто спрашивает… — перечисли только названия…") is the *prompt*, not the *acceptance criterion*. Acceptance criterion #1 ("contains all 3 names and none of the prices/descriptions") and #2 ("includes the `price_text` for 'маникюр' specifically, no other services' prices, and no full data dump") are testable as assertions, but "no full data dump" is fuzzy — what defines "data dump"? A character count threshold? Number of fields surfaced? Without a bound, this risks the "system handles X gracefully" pattern the rubric flags.

**Audit log boundary undertested.** FR-24 commits to "the price and description **values** are never logged" — but there's no acceptance criterion asserting that a structured-log search for the literal price string returns zero hits. Given that this is the same NFR-3-aligned redaction discipline as FR-18's token-handling, it deserves a test as explicit as "logs do not contain the price_text or description literal."

### Findings
- **high** Service-name uniqueness undefined (FR-23, FR-24) — `name` is required but no per-project uniqueness constraint is stated, and `ProjectServiceRepository.upsert`'s key is never named. Duplicate-name behavior on `/service remove`, `удали услугу`, FR-25's tailored answers, and FR-22's lemma-match all become ambiguous. *Fix:* add an explicit acceptance: "Unique on `(project_id, lower(name))`; upsert key is `(project_id, lower(name))`; duplicate-name attempts via slash/NL update the existing row and log `services_upsert_duplicate_name`."
- **high** NL remove/edit target resolution undefined (FR-24) — `удали услугу <name>` and `измени услугу` triggers are listed but the acceptance criteria say nothing about how an ambiguous name (multiple matches) or a no-match is handled. *Fix:* mirror FR-22's "one clarifying turn → escalate / fail-closed" pattern, or commit to "fail closed with 'не понял, уточните' if name doesn't resolve to exactly one row."
- **medium** Migration idempotency under-specified (FR-23) — "idempotent ALTER" claim doesn't match SQLite ALTER semantics. *Fix:* spell out the migration path (e.g., "if `project_services` exists, skip rename; else rename. Then for each new column, ADD COLUMN IF NOT EXISTS via column-existence pragma check"), and add an acceptance for "running the migration twice is a no-op on the second run."
- **medium** NL session lifecycle gaps (FR-24) — no acceptance for expired-session cleanup, concurrent pending sessions per operator/project, or TTL-expiry UX (does the bot tell the operator their request expired?). *Fix:* state the policy explicitly ("at most one pending session per (project, operator); a second `добавь услугу` while one is pending replaces it and tells the operator; expired sessions are not surfaced to operators and may be lazily reaped").
- **medium** Audit value-redaction lacks a verifying acceptance (FR-24) — the commitment "the price and description values are never logged" is strong, but no acceptance test asserts it. *Fix:* add acceptance "Structured-log search across `services_nl_op_*` events for the literal `price_text` and `description` substrings returns zero matches in unit and integration tests."
- **medium** "No data dump" is unbounded (FR-25) — acceptance criterion 2 forbids a "full data dump" without defining the bound. *Fix:* replace with a concrete bound, e.g. "the response references at most one service's `price_text` and at most one service's `description` when the question names a single service."
- **low** NL regex coverage acceptance is a floor (FR-24) — Cyrillic dash variants, "ё"/"е" normalization, multi-word service names, and name/description delimiting under absent `описание:` aren't addressed. *Fix:* add a small list of explicit "must parse" examples and "must fail closed" examples to the acceptance, so test data drives the regex.

## Scope honesty — adequate

Epic 12's "Explicitly deferred" list in the decision log is honest and concrete: LLM-based PDF extraction, web admin UI for `project_services` CRUD, multi-operator/multi-calendar (carried over from Epic 11), and booking. These are exactly the kind of "could be silently assumed" omissions the rubric wants flagged.

What's missing in the PRD body itself (vs the decision log) is similar `[NON-GOAL for MVP]` or `[NOTE FOR PM]` callouts. The Feature Group preamble and FRs read as a clean spec; a reader who only reads §4 won't learn that an admin web CRUD UI is out, or that LLM PDF→`project_services` extraction is deferred. These deferrals only surface in the decision log. For a green-light-to-build PRD, the deferrals should be visible from the PRD body too.

There are no `[ASSUMPTION: …]` tags on FR-23/FR-24/FR-25 — fair, since the decision log shows most choices are user-confirmed. The "one release" deprecation framing (called out under Decision-readiness) is the one place an `[ASSUMPTION]` would be appropriate.

### Findings
- **medium** Deferrals from the decision log don't reach the PRD body (§4 Epic 12 group) — LLM PDF extraction, admin web CRUD UI, multi-operator/multi-calendar are listed only in `.decision-log.md`. *Fix:* add a short "Out of scope for Epic 12" bullet list to the Feature Group preamble, mirroring the §2.2 / FR-18 calendar Non-Goals style.
- **low** "One release" deprecation window is an implicit assumption (FR-23, FR-24) — *Fix:* tag as `[ASSUMPTION: deprecation aliases removed in the next epic to touch this area; no fixed calendar date]` and index in the Assumptions Index if one exists.

## Downstream usability — adequate

The IDs continue contiguously (FR-23/FR-24/FR-25 follow FR-22 cleanly). §6 Data Requirements has a new row for `semantaix_calendar.db` (Epic 12) listing `project_services` and `services_nl_op_sessions` with column-level detail — good source-extract material for architecture and story creation. The Glossary (§11) adds "Project service" and "Schedulable service" with explicit ID-cross-references ("This is the sense used in **FR-19/FR-20/FR-22**"). This is the rubric's "every domain noun used identically across FRs" served well.

Cross-references resolve: FR-23 references FR-22 implicitly via `name` resolution, FR-24 references Epic 10 `operators` table by name, FR-25 references `_catalog_digest.get_digest(...)` and `GroundedRagAnswerer` by code-path name. The Delivery Mapping (§10) does **not** yet mention Epic 12, however — a downstream consumer reading §10 would not learn that Epic 12 exists or where its stories will live.

The FR-23 row in §6 collapses the two Epics' `semantaix_calendar.db` entries onto two separate table rows (Epic 11 and Epic 12), which is readable but slightly duplicative since they share a database file. Acceptable.

### Findings
- **medium** §10 Delivery Mapping omits Epic 12 — §10 ends with the Epic 11 reference and "to be created by `bmad-create-epics-and-stories`". Epic 12 is referenced everywhere except here. *Fix:* add a paragraph to §10: "Unified Project Services Catalog (FR-23–FR-25) is planned as Epic 12, depends on Epic 11 (table rename target), Epic 10 (operator registry for authorization), Epic 09 (operator command surface); see `epics/epic-12-unified-project-services-catalog.md` (to be created)."

## Shape fit — strong

Epic 12 is the right shape for the product: an internal/brownfield Russian-first bot adding a structured surface that two existing features (catalog answer, calendar) consume. There are no consumer-facing UJs needed — this is an operator-and-system spec — and the PRD correctly doesn't manufacture any. Personas are not touched (the existing Operator persona covers it). NFRs are unchanged (Epic 12 inherits Epic 11's NFR-3 OAuth/secrets posture and adds no new ones), which is correct: the FR-24 audit-keys-only rule is the only security-shaped behavior and it's stated inline in the FR.

The brownfield obligations the rubric calls out (existing-code references must be accurate) are met: FR-23 names `calendar_service_rules`, `.data/semantaix_calendar.db`; FR-24 names `nl_knowledge_ops`, `admin_nl_dialog`, `admin_nl_op_sessions`; FR-25 names `_catalog_digest.get_digest`, `GroundedRagAnswerer`, `grounding_system`, `_skip(reason='catalog_empty')`. Anyone touching the code can grep these names directly.

No findings.

## Mechanical notes

- **Glossary drift:** "service" is now disambiguated across "Service (microservice)" / "Project service" / "Schedulable service" / "Calendar operator" — good. One residual: FR-22 still says "lemma matching" on services without referencing whether the lookup runs against the unified `project_services` table or just its calendar-eligible subset. Reading FR-22 + FR-20 + FR-23 together, the answer is "calendar-eligible subset only" (since FR-22 lives in the calendar group), but FR-22 doesn't say so directly anymore now that the table is shared.
- **ID continuity:** FR-23/FR-24/FR-25 contiguous; no gaps; cross-refs to FR-18/FR-19/FR-20/FR-22 resolve.
- **Cross-references:** FR-25 references `source_id` patterns `catalog_digest:<project_id>` and `project_services:<project_id>` — these are concrete enough to test but should appear in the §6 / architecture notes for traceability. Currently they only appear inline in FR-25 acceptance.
- **Assumptions Index roundtrip:** no Assumptions Index appears in the PRD; the bootstrapped decision log carries the equivalent. Consistent with prior epics.
- **Decision-log/PRD alignment:** the decision log entry for Epic 12 cleanly mirrors FR-23/FR-24/FR-25. The "Conflicts surfaced — *(none)*" claim is mostly correct, but worth flagging two near-conflicts the log doesn't address:
  1. **Permission-model framing differs from FR-21.** FR-21 (Epic 11) says "operator AND admin can disable" the calendar feature *unconditionally*. FR-24 (Epic 12) says "operator AND admin can add/edit/remove services on a project where they are registered as an operator." The Epic 12 admin gate is narrower (admin-as-registered-operator) than Epic 11's admin gate (admin-as-admin). Not necessarily wrong — services editing is closer to content-curation than to enablement — but the asymmetry should be acknowledged so an implementer doesn't conflate the two.
  2. **`/calendar_service` alias and the recent Epic 11 follow-up.** The 2026-05-24 decision-log entry on Epic 11 dropped `/calendar_on` (auto-enable on connect). FR-24 retains `/calendar_service` "for one release as a deprecation-logged alias." Both moves are consistent in spirit (collapse redundant operator surfaces), but the deprecation timeline is now load-bearing because the same operator may have learned `/calendar_service` recently. Worth a sentence on user comms ("when an operator runs the deprecated `/calendar_service`, the bot reply suggests `/service`").
