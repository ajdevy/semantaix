# Validation Report — Semantaix PRD (Calendar Availability & Scheduling)

- **PRD:** `_bmad-output/planning-artifacts/PRD.md`
- **Rubric:** `.claude/skills/bmad-prd/assets/prd-validation-checklist.md`
- **Run at:** 2026-05-22
- **Grade:** Fair (PRD body strong; new calendar FRs carry blocking spec gaps)
- **Reviewers:** rubric-walker, adversarial-general

## Overall verdict

The existing PRD body is decision-ready and the new Calendar feature group (FR-18–FR-21) is unusually strong on its **safety thesis** — escalate-on-uncertainty, read-only OAuth scope, encrypted token storage, default-off gating — and faithfully mirrors the decision log with **no conflicts** to prior decisions. However, the security/crypto prose outruns the *product* spec: the adversarial pass rates the feature **NOT READY for architecture** because four load-bearing product questions are unanswered — **whose** calendar answers a question (multi-operator), **which** service a free-text Russian question maps to, **whose** timezone interprets the customer's time, and what happens when a token is revoked **on Google's side**. Two operational time-bombs the "read-only first" framing hides (Google OAuth app-verification for the sensitive scope; refresh-token long-term expiry) also need to be named as release dependencies.

## Dimension verdicts (rubric-walker)
- Decision-readiness — strong
- Substance over theater — strong
- Strategic coherence — strong
- Done-ness clarity — adequate
- Scope honesty — strong
- Downstream usability — adequate
- Shape fit — strong

## Findings by severity

### Critical (4) — resolve before epic-11 stories
- **[Adversarial] C1 — Multi-operator selection undefined** (FR-18/FR-19/FR-20/§6). Tokens stored per-operator and §10 cites multi-operator scoping, but FR-19 says "an operator" (singular) and nothing states *which* operator's calendar answers a given customer question. Blocks the data model (`calendar_service_rules`) and the answerer. *Fix:* state the rule — service binds to a specific operator, or union/intersect across connected operators — and define zero-match / multi-match behavior.
- **[Adversarial] C2 — "Service" entity + free-text→service mapping undefined** (FR-19/FR-20). Customer types free Russian ("можно записаться на маникюр в субботу?"); no requirement covers how that resolves to a `calendar_service_rules` row (exact vs lemma match via `RussianNormalizer`), nor no-match / ambiguous-match / no-service-named behavior. The hardest part (RU intent+entity extraction) is acceptance-criteria-by-assumption. *Fix:* add an FR for service resolution + the no/ambiguous-match paths.
- **[Adversarial] C3 — Customer timezone source unspecified** (FR-19/§8). PRD handles project/operator tz but not the *customer's*; Telegram doesn't expose it. "в 3 часа" = 3pm in what zone? Boundary answers become coin-flips, feeding the wrong-availability counter-metric. *Fix:* state a rule (e.g. all times interpreted in project tz; ambiguous customer-local times escalate).
- **[Adversarial] C4 — Google-side token revocation has no detection/recovery/notification** (FR-18/FR-21/NFR-3). If a user revokes access in Google, the next call 4xxs; PRD routes the customer to HITL but never (a) notifies the operator to reconnect, (b) says whether the token row is deleted or left as a poison row escalating forever, or (c) emits an incident — though project-context mandates Epic-02 incident integration. *Fix:* add detection-on-next-use → reconnect-state + operator notification + incident emission + token cleanup.

### High (6)
- **[Rubric+Adversarial] Multi-operator & timezone** — also surfaced by the rubric as its two High findings (FR-19/FR-20 multi-operator selection; project-timezone source/storage). Folded into C1/C3.
- **[Adversarial] H1 — Google OAuth consent-screen verification ignored.** `calendar.readonly` is a **sensitive scope**: an unverified app shows a warning interstitial and is capped to ~100 test users until it passes Google's OAuth verification (brand review, possibly CASA). Multi-week external dependency; unaddressed → operators can't connect in production. *Fix:* add a release-readiness dependency in §9 + NFR-3.
- **[Adversarial] H2 — OAuth `state` TTL/storage/single-use unspecified.** "Validates state" without binding + TTL + single-use is untestable; mirror the existing 5-min-TTL+attempt-cap login-code pattern. §6 lists no pending-OAuth-state table. *Fix:* specify `state` contract + storage.
- **[Adversarial] H3 — Refresh-token long-term expiry unhandled.** Refresh tokens die after 7 days while app is in "Testing" status, after 6 months unused, and under per-client token caps. Combined with H1, operators silently disconnect weekly. *Fix:* state handling + tie to the verification dependency.
- **[Adversarial] H4 — Multi-operator token collision / reconnect overwrite semantics.** `calendar_operator_tokens` keyed by project+operator — re-consent upsert? operator in two projects? two operators sharing one Google account? Data-integrity requirement absent.
- **[Adversarial] H5 — FR-21 "no measurable latency" guarantee is premature.** project-context says the architect must still decide standalone-answerer vs scheduling_context signal; those have different ordering/latency, yet the PRD pre-promises "config check precedes intent detection." Decide placement or soften the guarantee; "no measurable latency" needs a number.
- **[Adversarial] H6 — Callback public exposure & identity binding.** The browser hitting the callback is NOT authenticated as a Telegram user, so FR-18's "authenticated operator matches" overstates — the only binding is `state` (see H2). No requirement on rate-limiting the unauthenticated token-exchange endpoint or what it renders to the browser. *Fix:* requirements for rate-limit, browser response, and the state-based identity binding.

### Medium (6)
- **[Rubric+Adversarial] Customer-facing wording language** — FR-19/FR-21 quote English strings ("calendar isn't connected yet", "let me check and get back to you") while FR-19 mandates Russian; per the Russian-first-content-is-DATA rule these belong in `data/*`. *Fix:* state quoted strings are illustrative; actual copy is configurable Russian.
- **[Adversarial] M1 — Free/busy result staleness/caching TTL unspecified** (distinct from access-token caching). If freeBusy results are cached, a just-booked slot still reads free. *Fix:* state freshness/TTL or "one live call per question."
- **[Adversarial] M2 — Working-hours/service-days granularity vague** — days-of-week vs specific dates vs holiday closures (the repo already has the `holidays` lib) vs multiple windows/day (lunch). AC omits "dates" though FR text introduces it.
- **[Adversarial] M3 — "Duration"/slot-fitting semantics undefined** — to answer "free at 15:00?" you need a free block of `duration`; "relevant window" is undefined, so FR-19 AC isn't deterministically implementable.
- **[Adversarial] M5 — Escalation routing unspecified** — does a calendar escalation route to the *connected calendar operator* (who owns the calendar) or the project's `hitl_primary_operator`, and with what context? Interacts with C1.
- **[Adversarial] M6 — Alerts/incidents not extended for calendar failures** — FR-8 incident types + §7 got no calendar additions, despite the "every epic integrates with incidents (Epic 02)" rule. Overlaps C4.
- **[Rubric] No Glossary; "service" overloaded** — "service" = microservice vs bookable offering; "project"/"operator"/"tenant" undefined load-bearing nouns. *Fix:* add a short Glossary.

### Low (6)
- **[Rubric] No Open Question/[NOTE FOR PM]** flagging the multi-operator decision as undecided.
- **[Rubric] freeBusy look-ahead horizon** unspecified (how far forward can a customer ask?).
- **[Rubric] NFR-4 still a placeholder** (no latency/throughput numbers; FR-21 "no measurable latency" has no baseline).
- **[Rubric] FR-15 "Tenant-Scoped" terminology lag** vs §2.2 project-scoping reconciliation (cosmetic).
- **[Adversarial] L1 — Disconnect: Google-side revoke vs local delete** — is calling Google's revocation endpoint mandatory; what if revoke fails but local delete succeeds?
- **[Adversarial] L2 — "bookable" wording leaks write-intent** (FR-20) — prefer "schedulable"/"offered" to avoid implying booking is in scope.
- **[Adversarial] L4 — Multiple Google calendars per account** — which calendar(s) define "busy"? Primary only / all / operator-selected? Defaults to "primary" by accident.
- **[Adversarial] L3 — "availability-answer rate vs escalation" metric lacks a target/baseline.**

## Mechanical notes
- **ID continuity:** FR-1–FR-21 contiguous and unique; NFR-1–NFR-7 contiguous. Cross-refs resolve internally.
- **Decision-log alignment:** all new FRs/NFR-3/§2.2/§6/§7/§8/§10 match the 2026-05-22 session entries. No conflict with bootstrapped decisions; the multi-tenant Non-Goal supersession is reconciled in both PRD and log.
- **Glossary:** absent; "service" overload is the sharpest case.
- **Forward ref:** `epic-11-calendar-availability-scheduling.md` (§10) not yet created (expected).

## Reviewer files
- `review-rubric.md`
- `review-adversarial-general.md`
