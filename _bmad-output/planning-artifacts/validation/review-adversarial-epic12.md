# Adversarial Review — Unified Project Services Catalog (FR-23–FR-25, Epic 12)

Reviewer stance: red-team. Scope: the new Feature Group "Unified Project Services Catalog" (PRD §4 line 335 onward), FR-19/FR-20 edits, §6 additions, §11 Glossary additions, and the 2026-05-24 Epic 12 decision-log entry. Cross-checked against Epic 11 (FR-18–FR-22, NFR-3) and the binding rules in `_bmad-output/project-context.md`.

**Verdict: NOT READY for architecture. The Epic 12 prose is clean and the "Conflicts surfaced — *(none)*" claim is *almost* true, but four substantive holes will bite during implementation: (1) the permission model contradicts Epic 11's careful split between reversible and destructive ops without acknowledging it; (2) service identity is undefined at the column level (no uniqueness, no upsert key), which makes lemma-match, `/service remove`, and "сколько стоит маникюр?" all behaviorally ambiguous; (3) the NL extractor is regex-on-untrusted-Russian-text with a confirm-then-apply flow whose threat model is asserted as "mirrors `admin_nl_dialog`" but several attack surfaces (replay across operators, control-char injection into the preview DM, concurrent pending sessions) are unspecified; (4) FR-25's "fall back only if empty" is a binary cliff that silently regresses every project that has both PDF-built `catalog_digest` content AND a partial `project_services` row.**

---

## CRITICAL

### C1. [critical] Permission model silently contradicts Epic 11's "destructive = operator-only" split. (FR-24 line 361; cross-ref FR-18 line 265, FR-21 line 310, decision-log lines 80, 86–93)

Epic 11 enforces a sharp two-tier permission split, *deliberately*: **disable** (reversible — keeps the token) is operator-AND-admin; **disconnect** (destructive — deletes the token) is operator-only, "an admin cannot disconnect the operator's connected calendar." This is restated in three places (FR-18 line 265, FR-21 line 310, the 2026-05-24 decision-log entry line 89). The rationale, in the log: "an admin can pause an integration without the operator's involvement" but cannot perform irreversible writes against operator-owned data.

FR-24 line 361 then declares, without referencing this rule: **"Both operator AND admin can add/edit/remove services on a project where they are registered as an operator."** Note what this does:

1. It collapses the Epic 11 split. An admin who is registered as a project operator can now `/service remove маникюр` and delete operator-curated data. That is a destructive write — analogous to disconnect, not to disable — and Epic 11 said admins must not do destructive writes on operator-owned assets.
2. The qualifier "where they are registered as an operator" is *narrower* than Epic 11's admin gate (Epic 11's `/calendar_off` admin path does NOT require the admin to be a registered project operator), so the permission surface is now non-uniform across Epic 11 and Epic 12. The rubric reviewer flagged this exact "asymmetry" at the end of `review-rubric-epic12.md` (mechanical notes #1) but rated it as low severity. It is not low. An implementer reading FR-21 + FR-24 has to invent the policy ("does an admin who is NOT a registered project operator get 403 or 200 on `POST .../services`?") — and there is no acceptance criterion either way.
3. The decision log's "Conflicts surfaced — *(none)*" claim (line 117) is therefore wrong. The conflict exists and was not addressed.

The right shape, by direct analogy with FR-18/FR-21:
- `/service add` and `/service edit` (non-destructive — additive/idempotent state) → operator-AND-admin (analogous to enable/disable).
- `/service remove` (destructive — irrecoverable loss of operator-curated price/description text) → operator-only (analogous to disconnect).

If the team intends the FR-24 collapse on purpose, the decision log needs an entry that says *"we are explicitly relaxing Epic 11's destructive-op rule for services because services are project-owned-content, not operator-owned-credentials, and the rationale is X"*. As written, FR-24 just reads "and admins too" and walks past the asymmetry.

### C2. [critical] No per-project uniqueness on `name` and no upsert key — the entire FR-25 / FR-22 / `/service remove` story is ambiguous. (FR-23 line 342; FR-24 line 359; FR-25 lines 382–384; cross-ref FR-22 line 323)

FR-23 enumerates columns and asserts `name (REQUIRED)`. No uniqueness constraint is stated, no index is named beyond `project_services_project_idx` (which is plainly a btree on `project_id` only). FR-24 says `ProjectServiceRepository.upsert` is the canonical seam without naming the upsert key. This cascades:

- **FR-25 acceptance is unimplementable as written.** Acceptance #2 (line 383): `"Сколько стоит маникюр?"` → the answer includes "the `price_text` for 'маникюр' specifically." With no uniqueness on `(project_id, name)`, an operator can legitimately have two rows both named "маникюр" (different durations, different prices — the schema permits it). Which price is "for 'маникюр' specifically"?
- **FR-22 service resolution becomes nondeterministic.** FR-22 lemma-matches and "never guesses." Two rows that lemma-collapse to the same form (`стрижка мужская` and `стрижка детская` both contain the lemma `стрижка`; `маникюр` and `маникюр-классика` share `маникюр`) make every customer "сколько стоит стрижка?" an ambiguous-match → forced clarification. Was this priced in? FR-22 acceptance contemplates "ambiguous match" but doesn't say the lemma-matcher must run against the project-specific catalog with project-defined name conventions. Worse, the lemma-match seam is the same `RussianNormalizer.lemmas` whose dictionary is **global** — an operator naming a service "запись" is going to lemma-collide with the scheduling-intent regex itself.
- **`/service remove <name>` and `удали услугу <name>` have no defined behavior on multi-match.** FR-24 acceptance is silent. The rubric reviewer flagged this as "high" — it is. Without explicit "fail-closed-if-not-exactly-one" or "operator must pick by id," the implementer will invent: delete-first-match (silent data loss), delete-all-matches (worse silent data loss), or 400-with-list (the only safe option).
- **Lemma cache invalidation on rename is unspecified.** If a service is renamed from "маникюр" to "маникюр-классика" via NL `измени услугу`, does FR-22's lemma cache invalidate? FR-22 doesn't say there *is* a cache, FR-24 doesn't say the upsert busts one, and the architecture doc (not re-read here) is the last place this could live. There will be one.

The fix is small: declare `UNIQUE(project_id, lower(name))`, declare `ProjectServiceRepository.upsert` keyed on `(project_id, lower(name))`, add an acceptance "duplicate-name attempt updates the existing row," and explicitly state that FR-22 lemma-match runs against the project-scoped `project_services` rows with "ambiguous → one clarifying turn → escalate" inherited from FR-22 verbatim.

### C3. [critical] FR-25's "fall back only if empty" is a behavior cliff that silently regresses partially-migrated projects. (FR-25 line 377)

FR-25: *"If `project_services` is empty for the project → fall back to the existing `_catalog_digest.get_digest(...)` LLM path."* Binary. The decision-log entry (line 111) restates it: "When `project_services` is non-empty for `ctx.project_id`, render as a labelled all-fields plain-text chunk … When empty, fall back."

The brownfield reality: every operator on every existing project today has a `catalog_digest` built from their `/kb_add`-uploaded PDFs. The day an operator adds ONE row via `/service add маникюр duration=60` (intending to make it calendar-bookable), FR-25 immediately stops consuming the digest for *every* catalog answer on that project. A customer asking "какие услуги?" gets back **only "маникюр"** even though the PDF lists 12 services. The catalog answer regresses silently the moment a single calendar-eligible row is added. This is the rubric's "binary cliff" risk made concrete.

Three sub-problems:
1. **The decision was never named as a trade-off.** The decision log says "Catalog answer reads structured services first" but doesn't acknowledge that this *demotes* the digest from an authoritative source to a fallback the moment any row exists. An operator's mental model is "I added a row, my catalog gained a row," not "I added a row, my catalog shrank to one row."
2. **The acceptance test cooperates with this regression.** AC #3 line 384: "after adding one service row, the next answer shows `source_id` `project_services:<project_id>`." That asserts the source switch, not that the answer is *better* — and as designed, the answer is worse.
3. **There's no transition story.** FR-25 doesn't say "operators must migrate the full PDF content before adding a row," doesn't say "the catalog answer merges both sources during transition," doesn't say "we proactively warn the operator." It just flips the source and walks away.

Pick one:
- (a) Merge: render both `project_services` rows AND `catalog_digest` content in one labelled chunk, until the digest is explicitly retired per-project.
- (b) Guard: refuse to switch to the structured path until `project_services` row count ≥ some operator-confirmed completion signal (e.g., `catalog_source_locked=1` in `calendar_project_settings`).
- (c) Warn: on first row insert when `catalog_digest` is non-empty, DM the operator "your catalog answer will now use the structured table only — please confirm by /service list."

Whichever is chosen, "single-row insert silently shrinks the catalog answer from N items to 1" cannot ship without an explicit decision on top.

### C4. [critical] NL dialog threat model is asserted as "mirrors `admin_nl_dialog`" but the threats unique to a *services* dialog are not enumerated. (FR-24 lines 357–359)

FR-24 buys correctness-via-precedent: state machine, TTL 600s, `secrets.token_urlsafe(16)`, atomic `consume` via `hmac.compare_digest`. Good defaults. But the dialog is different from `admin_nl_dialog` in three ways that matter:

1. **The preview message is operator-supplied content rendered back to the operator's Telegram client.** FR-24: *"the bot DMs a Russian preview such as 'Создать услугу «маникюр» …'"*. The "маникюр" comes from operator-typed text. The preview is then re-parsed on `да`/`/confirm`. What if the operator (or a coerced operator account) types `добавь услугу «"><script>` or `добавь услугу маникюр\n\nдобавь услугу педикюр` (newline-injection) or a 4-KB name? Telegram's MarkdownV2 has reserved chars that will either break the preview render or — worse — escape into formatting that misrepresents what's about to be applied. The "preview" then no longer reflects the action. FR-24 says nothing about sanitization, length caps, or rendering mode (plain text vs MarkdownV2 vs HTML).
2. **`confirm_token` ownership and replay are not scoped.** FR-24 says the token is single-use and 600s TTL. It doesn't say the token is bound to **(operator_id, project_id)**. The endpoints (line 358) take `{project_id}` and `{session_id}` in the path and the token in the body. Concretely: if operator A creates a `pending_confirmation` session for project P with token T, and operator B (who is also registered on P) somehow obtains T (shared bot DMs, screenshot, replay from logs), can operator B's `/confirm T` apply operator A's pending intent? The bot side will gate by sender — but the API endpoint is "behind `internal_service_token` auth" (line 358), which means it trusts the bot's `as_user=` claim. If the bot's NL dispatch doesn't verify *"the session's originating operator == the current sender"* before calling confirm, there is a cross-operator replay path through the bot. FR-24 doesn't require that check.
3. **Concurrent pending sessions per (operator, project) are unspecified.** Operator types `добавь услугу маникюр …`, gets preview, doesn't reply. Then types `добавь услугу педикюр …`. Two pending sessions exist. Which one does plain `да` confirm? Telegram-side state is fragile (replies in DMs may not have a `reply_to`). The rubric reviewer flagged this as "medium"; given that "wrong confirm" writes durable catalog data, it is closer to "high/critical." FR-24 needs an explicit policy: "at most one pending session per (operator, project); a second trigger replaces the first and tells the operator."

These are not theoretical. The catalog answer feeds the customer-visible response; an operator who confirms the wrong preview ships wrong prices to every customer until the next edit.

---

## HIGH

### H1. [high] Migration "idempotent ALTER" claim is technically wrong and the production-empty justification doesn't generalize. (FR-23 line 341; decision-log line 104)

SQLite `ALTER TABLE … RENAME TO` is not idempotent: a second run raises `no such table: calendar_service_rules` (and depending on order, either succeeds because the new table exists, or errors). `ALTER TABLE … ADD COLUMN` is not idempotent either — a second run raises `duplicate column name`. The phrase "idempotent ALTER" is doing real damage here because it lulls the implementer into omitting the pragma-driven "if column exists, skip" guard.

The "the table is empty in production — zero data risk" justification (FR-23 line 341, decision log line 104) only addresses **production**. The repo runs the same migration in CI (where tests may have populated `calendar_service_rules` before the rename runs), in dev (where local developers may have run Epic 11 migrations on a populated DB), and in fresh-deploy scenarios where the table doesn't exist at all (should the migration create it from scratch, or assume Epic 11 ran first?). FR-23 covers exactly one of those: production-empty. The decision log even acknowledges this as a "production-empty" claim without contemplating other deployments.

This is also the kind of thing where "we'll handle it in the story" silently means "we'll discover it on the first re-run." The FR needs:
- Explicit idempotency contract: "running the migration twice on the same DB is a no-op the second time."
- Fresh-deploy path: "if `calendar_service_rules` does not exist, create `project_services` directly with the final schema; do not require Epic 11's rules-table migration to have run first."
- An acceptance test that runs the migration twice.

### H2. [high] "One release" deprecation window is undefined, and the previous Epic 11 follow-up just *deleted* a deprecated command without a deprecation period at all. (FR-23 line 344; FR-24 line 355; cross-ref decision-log lines 86–93)

FR-23 keeps `CalendarSettingsRepository`'s service-rule method aliases "for one release." FR-24 keeps `/calendar_service` "for one release as a deprecation-logged alias." The repo has no documented release cadence — no version bump in `pyproject.toml` correlates to "releases," and "one release" never appears in any prior PRD or decision log entry as a defined unit.

Worse: the immediately-prior decision-log entry (2026-05-24, Epic 11 follow-up, lines 86–93) **deleted** `/calendar_on` and `POST /enable` outright with no deprecation period — "we don't need a separate /calendar_on command." That establishes a precedent that "one release" in this codebase can mean "zero," and an implementer reading both decisions together has no way to know whether `/calendar_service` survives the same merge it's added next to or for some unspecified number of weeks.

Concrete operator harm: the same operators learned `/calendar_service` recently as part of Epic 11. If `/calendar_service` is silently removed two weeks later, those operators hit "unknown command" with no migration message. FR-24's "deprecation-logged alias" wording says *logging* — it does NOT say the operator gets a "use `/service` instead" reply in Telegram. The rubric reviewer's mechanical note (#2) flagged this. It needs an answer in the FR, not in a story.

Fix: pin "one release" to a concrete trigger ("removed in the Epic 13 cleanup PR" or "removed after 30 days post-merge"). Require the deprecated paths to DM a user-facing migration hint, not just log.

### H3. [high] Concurrent edits between two operators on the same project are unspecified — race, lost-update, and resurrect-after-delete are all unhandled. (FR-23 line 342; FR-24)

FR-23 and FR-24 say nothing about concurrency. Real scenarios that will happen on a shared project:

1. **Same-row race.** Operator A: `добавь услугу маникюр на 60 минут цена 2000` (creates pending session A). Operator B (before A confirms): `добавь услугу маникюр на 90 минут цена 3000` (creates pending session B). Both confirm within seconds. With no uniqueness on `(project_id, name)` (C2), two rows are created — both named "маникюр." With uniqueness, the second confirm either fails (operator gets a cryptic 409) or overwrites (lost update). Neither is specified.
2. **Add-vs-delete race.** Operator A: `/service add маникюр duration=60`. Operator B (simultaneously): `/service remove маникюр`. With no ordering guarantee, the resulting state is "depends." FR-24's acceptance doesn't cover it.
3. **`updated_at` is for what?** FR-23 lists `updated_at` but no FR says it's used for optimistic locking. Without it, there's no "stale write" defense. Two operators editing the same row through two pending NL sessions will silently overwrite each other on confirm.

The repo already has the right pattern: per-operator `asyncio.Lock` single-flight (used for token refresh per project-context line 61). It would extend straightforwardly to a per-(project_id, name) lock around `ProjectServiceRepository.upsert`. But that requires the FR to either name uniqueness (C2) or name the locking strategy. As written, it does neither.

### H4. [high] FR-25's structured-chunk rendering leaks field labels into the LLM input, and the existing 4-layer grounding gate has never seen labelled prose. (FR-25 line 375)

FR-25 renders rows as a "labelled, all-fields plain-text chunk (`Название / Описание / Цена / Длительность / Дни / Часы` — one block per service)." The grounding LLM is then expected to "humanize" via the new `grounding_system` rule. The four existing grounding layers (strict-grounding LLM with `ESCALATE_TO_HUMAN` sentinel → LLM verifier → regex guardrails → profanity check, per `CLAUDE.md`) were tuned on PDF-derived prose chunks where labels do not appear. Two failure modes are now plausible:

1. **Label leak into the customer answer.** The model occasionally copies "Название: маникюр" verbatim — especially when the question is terse ("услуги?") and the simplest completion is "Название: маникюр, Название: педикюр, Название: стрижка." FR-25 acceptance doesn't check for absence of label tokens. The new `grounding_system` rule is a soft instruction in a Russian-language prompt; soft instructions break under terse questions.
2. **Verifier false-rejects.** The verifier (per `guardrails.py`) regex-checks for hedging / policy / length. A model output that says "У нас есть маникюр, педикюр, стрижка." is fine. But if the model emits "Стрижка от 1500 ₽." — the `₽` symbol, currency formatting, or the "от" hedge ("from") may trip the hedges regex (`data/russian_hedges.txt` includes phrases beginning with "от ..."?). FR-25 doesn't say the guardrails lists were audited for catalog-output compatibility.

The fact that the "no extra LLM call relative to today's digest path" wording in FR-25 (line 375) is true does NOT mean the *content profile* is the same — labels vs prose is a real distribution shift on the input. The risk is that the catalog answer either looks robotic ("Название: маникюр, Цена: 2000 ₽") or gets silently blocked by the verifier and falls through to HITL on every catalog question. Neither is covered by FR-25 acceptance.

Fix: either (a) render the chunk as natural Russian prose at the repository boundary ("Маникюр — 2000 ₽, 60 минут, пн–сб 10:00–19:00") and skip the label labels, removing the LLM's option to leak them; or (b) add an explicit acceptance test "the rendered answer contains no field-label substrings (`Название:`, `Цена:`, etc.)" and an explicit audit of `data/russian_hedges.txt` against typical price/duration phrasings.

### H5. [high] Audit "keys not values" is an over-correction that destroys the audit's primary purpose. (FR-24 line 362; decision-log lines 113, 117)

FR-24: *"every successful confirm logs `services_nl_op_confirmed` with `trace_id, project_id, operator, op_type, payload_keys` — the price and description **values** are never logged (they may carry sensitive data); only the **keys** present in the payload are."* The decision log doubles down (line 117): "logs **keys**, never **values**."

This is the wrong threshold for two reasons:

1. **`price_text` is not sensitive.** It is the customer-facing public price the operator is publishing to every customer who asks. The bot will read it back in catalog answers. Logging it is no more privacy-sensitive than logging the customer-facing prompt — which the repo already does (per `answer_traces`).
2. **The audit log is supposed to answer "what did the operator change?"** With keys only, an audit query for "who set маникюр's price to 9999 ₽ on 2026-05-30?" cannot be answered. The dispute / regulatory / "operator claims I never set that price" path is broken by design. The trace_id correlation lets you find the *bot DM* — but the bot DM is also the only place the value was committed, and there is no requirement that bot DMs are durable.

The framing "they may carry sensitive data" is borrowed from FR-18's token-handling discipline (NFR-3), where the sensitivity is unambiguous (a refresh token = standing access). Applying the same blanket rule to catalog text is cargo-culting the policy without re-evaluating the threat.

Fix: log `price_text` and `description` values in `services_nl_op_*` events (these are non-secret operator-published content), with a per-field allowlist (`secret_keys = []` for `project_services`). Reserve "keys not values" for fields that *are* secret-class. If there is a real reason to keep values out (e.g., GDPR posture on free-text description), state the reason explicitly in the FR and add a dispute-recovery path (e.g., "operator can re-fetch their own service history via `/service list --full`").

### H6. [high] FR-25 rendering uses field labels `Название / Описание / Цена / Длительность / Дни / Часы` — but `working_hours_json` and `service_days_json` aren't strings; the rendering shape and JSON-to-Russian conversion are unspecified. (FR-25 line 375; FR-23 line 342)

FR-23 stores `working_hours_json` and `service_days_json` as JSON blobs (the column names are explicit). FR-25 renders them under labels `Дни` and `Часы`. How? An operator entered `pn-sb` via slash or `пн-сб` via NL → it was serialized to `["mon","tue","wed","thu","fri","sat"]` or similar JSON. The rendering side must convert that back to a Russian-readable form. FR-25 says nothing about:
- The serialization format. (Day codes? Day full names? `working_hours_json` shape — `{"mon":[["10:00","19:00"]]}` or `[["mon", "10:00", "19:00"]]`?)
- The de-serialization for rendering. (Who owns the JSON-to-Russian map? Reuse from where?)
- Multi-window-per-day rendering. FR-20 (line 296) explicitly supports "**one or more per day**, e.g. to model a lunch break." How does that render? "пн 10:00–13:00, 14:00–19:00"? Two label rows?
- Date-exception rendering (`date_exceptions_json`). Not in the label list at all — silently dropped from the catalog answer?

The architecture step will invent a format, but the absence of a contract here means slash-command, NL preview, and catalog-rendering paths may invent three different formats. The Russian-first-content-is-DATA rule (project-context line 95) implies the day-name maps live in `data/*.json` — but FR-25 doesn't say where.

---

## MEDIUM

### M1. [medium] NL regex coverage is asserted "at minimum" without naming the failure-mode set. (FR-24 line 369)

FR-24 acceptance: *"Russian regex extracts at minimum: name; optional duration in minutes …; optional days range …; optional hours …; optional price …; optional desc."* The "at minimum" framing is a free pass. Real Russian utterances that the implementer will see on day one and that have no defined behavior:

- **"добавь услугу маникюр и педикюр"** (two services in one utterance) — single row? Two rows? Fail closed?
- **"добавь услугу маникюр, время как у педикюра"** (cross-reference to another service) — fail closed, presumably, but it's not stated.
- **"добавь услугу «маникюр + дизайн»"** (quoted name, punctuation in name) — regex name-capture termination is undefined; the `+` is regex-metachar-adjacent.
- **"переименуй услугу маникюр в маникюр-классика"** (rename intent — not in trigger list) — falls through silently? "Измени услугу" trigger word doesn't cover rename specifically.
- **"добавь услугу маникюр на полтора часа"** ("полтора" = 1.5; not a digit — duration extractor designed for digits only?) — fail closed?
- **Cyrillic dash variants** (`пн–сб` en-dash vs `пн-сб` hyphen vs `пн—сб` em-dash; same for `10–19`). The regex must normalize or accept all three.
- **"ё" vs "е" normalization.** Does `RussianNormalizer` apply at the regex stage or after? FR-24 doesn't say.

"Ambiguous fails closed" is correct policy but doesn't constrain *which* of the above triggers ambiguity vs unknown vs silent ignore. The rubric reviewer flagged this as low; given that operators will use Russian conversationally and the slash command is offered as the "precise" path, it is medium — the NL path's whole value proposition is that operators don't have to switch to slash.

### M2. [medium] FR-25 "no full data dump" lacks a measurable bound; the question-tailoring is judgment-shaped. (FR-25 lines 376, 383)

The Russian guidance rule extends `grounding_system` with: *"Если клиент просто спрашивает, какие есть услуги — перечисли только названия, естественно и кратко. Если клиент спрашивает про цену, детали, описание или конкретную услугу — добавь только то, что он спросил. Не дампи всё подряд."* The acceptance criterion echoes it: "no full data dump."

Both are soft. What is "data dump"? Three sentences? Four labels? The model is non-deterministic on this dimension. An acceptance test asserting "no full data dump" cannot be written without a bound. Choose one: a character cap, a field-count cap, or a regex that forbids the field-label tokens (which doubles as H4's fix).

### M3. [medium] FR-25 fallback path's source-id is named (`catalog_digest:<project_id>` vs `project_services:<project_id>`) but the answer-trace schema doesn't reserve these. (FR-25 line 384; cross-ref §6 row for `semantaix_answer_traces.db`)

FR-25 AC #3 says the trace `source_id` switches between two literals depending on the path. The §6 row for `semantaix_answer_traces.db` (line 432) just lists `answer_traces` as the table; the new source-id literals don't appear there or in the data dictionary. Architecture / story-time will have to invent the field name (`source_id`? `chunk_id`? `retrieval_source`?). Pin this in FR-25 itself: "writes `answer_traces.source_id` = …".

### M4. [medium] Bot triggers `добавь услугу`, `измени услугу`, `удали услугу` will collide with customer-side Russian. (FR-24 line 359)

The triggers fire on *operator* messages, but the bot's sender-routing branches on "is the sender a registered operator on this project." A customer accidentally messaging exactly "добавь услугу X" will (per FR-24 line 361 and acceptance #2) be silently ignored — fine. But the *operator-on-the-customer-channel* edge case: many real-world operators co-habit the customer-facing Telegram bot for testing or as their first contact. If the operator messages "добавь услугу маникюр" in the customer thread (intending a test), the bot creates a pending session and DMs them a preview. The customer thread now silently has a non-response from the bot ("trigger matched, but reply went to DM"). That's user-visible weirdness with no FR coverage.

Also: the trigger is a substring/keyword match (the FR doesn't say it's anchored to start-of-message). "Хочу добавь услугу" (typo / quoting) might match. State the trigger as start-of-message anchored, or explicit slash-then-NL like the rest of the operator surface.

### M5. [medium] `services_nl_op_sessions` carries `payload_json` "blob holds operator-typed structured intent and is **never logged**" — but the blob includes the price the operator typed, which IS what gets applied. (§6 line 441)

§6 says `payload_json` is "never logged." That's again the H5 over-correction: the payload IS the audit evidence of "what change is pending." If it's not logged at confirm time AND it's stored in the session table only until TTL/confirm/cancel, the after-the-fact reconstruction of "what did operator X confirm at 14:32" is unsupported. The session row is deleted/expired; the audit-log row has only `payload_keys`. The trace_id correlation lets you find the bot DM, which is the only forensic source. That's fragile.

Either log the payload at confirm time, or keep `services_nl_op_sessions` rows soft-deleted with the payload, or accept that audit is keys-only and write that limitation into the FR.

### M6. [medium] FR-23 doesn't say what happens to existing Epic 11 calendar tokens / settings when the rename runs. (FR-23 line 341)

The migration renames `calendar_service_rules` → `project_services` *within* `.data/semantaix_calendar.db`. That same DB holds `calendar_project_settings`, `calendar_operator_tokens`, `calendar_oauth_pending_state`. FR-23 doesn't say "those other tables are unchanged" — and given that the production-empty justification was for the rules table only, an implementer might reasonably wonder whether the broader rename touches related schemas (e.g., a foreign key from `calendar_project_settings` to `project_services`). State the negative: "no other tables in `semantaix_calendar.db` are altered by this migration."

### M7. [medium] FR-22 wording is now stale: "configured service" no longer implies `calendar_service_rules` and there's no requirement that FR-22 reads from `project_services` calendar-eligible subset. (FR-22 line 323 unchanged; cross-ref FR-23, FR-20)

FR-19 (line 280, edited for Epic 12) now says it sources from `project_services` rows where `duration_minutes IS NOT NULL`. FR-20 (line 296, edited) repeats. FR-22 (line 321–327) is unchanged: it talks about "configured service" without specifying the table or the eligibility predicate. The rubric reviewer's mechanical note #1 caught the same drift. An implementer reading FR-22 in isolation will assume "configured service" = "any `project_services` row," including catalog-only rows, and then the lemma-matcher will surface non-schedulable services for availability questions, which leads to "yes, маникюр exists" → user asks for time → "but I can't book it for you." Tighten FR-22 to say "configured **schedulable** service" with explicit reference to FR-23's calendar-eligibility predicate.

---

## LOW

### L1. [low] §10 Delivery Mapping still doesn't mention Epic 12.
The rubric reviewer flagged this; restating because it's a publication gap, not just a polish item. A downstream consumer reading §10 won't know Epic 12 exists or where its stories live. Add the paragraph.

### L2. [low] The `services_nl_op_sessions` table re-uses the shape of `admin_nl_op_sessions` but lives in a *different* SQLite file (`semantaix_calendar.db`, per §6 line 441) than the other NL ops session tables (`semantaix_nl_ops.db`, line 433).
Inconsistent "one-file-per-concern" application: NL ops sessions are spread across two DB files based on which feature owns the operator dialog. Pick one (probably `semantaix_nl_ops.db`, alongside `nl_op_sessions` and `admin_nl_op_sessions`) and document the rationale. As-is, an operator runbook for "show me all pending NL sessions" has to query two DBs.

### L3. [low] The decision-log entry's "Conflicts surfaced — *(none)*" claim (line 117) is overconfident — see C1, H5, H2.
The "I reviewed everything and there's no conflict" framing is the kind of move that ages poorly. At minimum, the asymmetry with FR-21's permission model (C1), the audit-keys-only inheritance from FR-18 without re-evaluating the threat (H5), and the "one release" vs the immediately-prior zero-deprecation-period Epic 11 follow-up (H2) deserved explicit confront-and-resolve entries.

### L4. [low] Glossary "Project service" entry doesn't disambiguate "calendar-eligible" vs "catalog-only" — the very split FR-23 introduces.
§11 line 497: "a canonical operator-curated row in `project_services` per project, carrying `name` (required) plus optional description / price / tags AND optional scheduling fields." That's accurate but flat. The implementer reading the glossary still has to flip to FR-23 to learn the eligibility predicate (`duration_minutes IS NOT NULL`). Add a sentence: "A project service is **catalog-eligible always** and **calendar-eligible iff `duration_minutes IS NOT NULL`** — see FR-23, FR-20."

### L5. [low] The decision log's "Operator-AND-admin can edit services" entry (line 109) repeats the FR-24 wording without surfacing that this is a *narrowing* of the Epic 11 admin gate.
Tiny doc hygiene — at minimum cross-reference the FR-21/FR-18 admin gate so a reader walking the log linearly knows the policy changed shape.

---

## Findings summary

- **Critical (4):** C1 permission model contradicts Epic 11; C2 no service-name uniqueness / undefined upsert key; C3 FR-25 binary cliff regresses partially-migrated projects; C4 NL dialog threat model under-specified (preview injection, cross-operator confirm replay, concurrent pending sessions).
- **High (6):** H1 migration "idempotent ALTER" technically wrong; H2 "one release" undefined and contradicted by immediately-prior precedent; H3 concurrent edits race / lost-update unspecified; H4 FR-25 structured-chunk rendering leaks labels and the existing 4-layer gate is unaudited for it; H5 "keys not values" audit destroys the audit's purpose; H6 JSON-to-Russian rendering of working hours / days / exceptions is unspecified.
- **Medium (7):** M1 NL regex "at minimum" leaves real utterances undefined; M2 FR-25 "no data dump" lacks measurable bound; M3 FR-25 trace source_id literals not pinned in §6; M4 NL trigger keywords collide with customer Russian + sender-routing edge case; M5 `payload_json` never-logged means audit is unreconstructible; M6 migration scope to other tables in `semantaix_calendar.db` unstated; M7 FR-22 wording is now stale w.r.t. FR-23's eligibility split.
- **Low (5):** L1 §10 Delivery Mapping omits Epic 12; L2 NL-ops session table placed in wrong DB; L3 "Conflicts surfaced — *(none)*" overconfident; L4 Glossary doesn't surface eligibility split; L5 decision log doesn't flag admin-gate narrowing.

**Total: 22 findings (4 critical, 6 high, 7 medium, 5 low).**
