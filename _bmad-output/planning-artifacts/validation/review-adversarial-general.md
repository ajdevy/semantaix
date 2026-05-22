# Adversarial Review — Calendar Availability & Scheduling (FR-18–FR-21)

Reviewer stance: red-team. Scope: FR-18–FR-21, NFR-3, §2.2 Non-Goals, §6/§7/§8/§10 calendar additions.
Reference: PRD.md and project-context.md.

**Verdict: NOT READY for architecture. The feature reads coherently at the prose level but every hard question — whose calendar, which service, whose timezone, what the customer hears — is left to the implementer. These are not implementation details; they are product decisions that block the data model and the answer-pipeline contract.**

---

## CRITICAL

### C1. [critical] "Multiple operators per project" is named as a problem and then never answered. (FR-18, FR-19, FR-20, §6)
FR-18 stores tokens "**per-operator**" and §6 keys `calendar_operator_tokens` "by project + operator". §10 says the feature "builds on … **multi-operator** scoping (Epic 10)." So a project can have N connected operators. But FR-19 says "compute candidate availability from **an** operator's Google Calendar" (singular, FR-18 line 246: "their own"). **Nothing in FR-18–FR-21 states how the system picks WHICH operator's calendar answers a given customer question.**

This is the single biggest hole. Possible interpretations all have different data models and different answers:
- (a) The project has exactly one "calendar operator" → then "multi-operator" framing is misleading and FR-20's "per operator where applicable" (line 275) is dead weight.
- (b) The service maps to a specific operator → then FR-20's service rules need an operator FK and there is no requirement saying so.
- (c) "Available" = ANY connected operator is free → then it's a union over freeBusy across operators, the "one freeBusy call per check" performance rule (project-context line 135) is violated, and the answer "yes, available" hides *which person*.

An architect cannot design `calendar_service_rules` or the answerer without this. **Resolve before any epic-11 story is written.**

### C2. [critical] "Service" is undefined as an identifiable entity and there is zero requirement for free-text → service mapping. (FR-19, FR-20)
FR-19 fires "when a customer asks an availability/scheduling question" and answers "intersected with the per-service rules of FR-20." FR-20 defines services by "**service name**, duration, working hours…". But a customer types free Russian text. **How does "можно записаться на маникюр в субботу?" resolve to the `calendar_service_rules` row named "маникюр"?**

There is no requirement covering:
- Exact match vs fuzzy/lemma match (the repo has `RussianNormalizer`, but no FR mandates using it for service-name resolution).
- What happens on **no match** (customer named a service that isn't configured) vs **ambiguous match** (two services match).
- What happens when the customer gives a date/time but **no service** at all.

FR-19 acceptance criteria silently assume "the service" is already known. The hardest part of the feature — intent + entity extraction in Russian — is unspecified. This is acceptance-criteria-by-assumption.

### C3. [critical] Customer-facing timezone is unspecified; the PRD only handles the project/operator side. (FR-19, §8 risks, NFR-?)
§8 risk says "Timezone/DST errors → … config-driven **project** timezone" and project-context line 129 explicitly raises "customer asks in one tz, operator calendar in another." **But no FR establishes where the *customer's* timezone comes from.** Telegram does not reliably expose a user's timezone. So when a customer says "в 3 часа", 3pm in *what* zone?

Without a stated rule (e.g. "all times are interpreted in the project timezone; ambiguous customer-local times escalate"), every availability answer near a working-hours boundary is a coin flip. project-context calls out the DST/cross-tz hazard but the PRD provides no requirement that closes it. This directly feeds C7 (the counter-metric).

### C4. [critical] Token revocation *on Google's side* has no detection or recovery requirement. (FR-18, FR-21, NFR-3)
FR-21(b) covers "token revoked" as a tri-state branch, and FR-18 says refresh failure "surfaces as a recoverable 'reconnect' state." But there is **no requirement that the operator is ever told their calendar silently stopped working.** If a user revokes access in their Google account settings, the next freeBusy/refresh 400s. The PRD routes the *customer* to HITL (good) but says nothing about:
- Proactively notifying the operator "your calendar disconnected, please /connect_calendar again."
- Whether a revoked token is deleted or left as a poison row that escalates every future calendar question forever.

Per project-context line 105 ("every epic integrates with the incident/alerts solution"), a token going dead is exactly an incident — but FR-18 never says revocation emits one. Result: silent, indefinite degradation that looks like "the bot just escalates a lot."

---

## HIGH

### H1. [high] Google OAuth consent-screen verification status is ignored — `calendar.readonly` is a SENSITIVE/RESTRICTED scope. (FR-18, NFR-3)
FR-18 scopes consent to `calendar.readonly`. Google classifies Calendar read scopes as **sensitive scopes**: an unverified OAuth app shows operators a scary "Google hasn't verified this app" interstitial AND caps the project at ~100 users / shows test-user-only behavior until the app passes OAuth verification (brand review, possibly a CASA security assessment). **Nothing in the PRD or NFR-3 acknowledges this gating.** This is a multi-week external dependency on Google's review process, not a code task. If unaddressed, operators literally cannot connect in production. Must be flagged as a release-readiness dependency.

### H2. [high] The OAuth `state` lifetime, storage, and single-use semantics are asserted but not specified. (FR-18, project-context L76)
FR-18 says the callback "validates the `state` parameter (CSRF)." Acceptance criteria says "a forged/expired `state` … is rejected." But **expired against what TTL?** Where is `state` stored (it must be server-side or signed to be validatable)? Is it single-use (consumed on first callback to prevent replay)? The HITL login-code pattern in the existing codebase has a 5-min TTL + attempt cap — the PRD should say `state` mirrors that, but it doesn't. "Validates state" without a binding+TTL+single-use contract is untestable hand-waving. Also: §6 lists no table for pending OAuth state — where does it live?

### H3. [high] Refresh-token long-term expiry (the 6-month / "testing app 7-day" rule) is unhandled. (FR-18, NFR-3)
FR-18 handles near-expiry **access** token caching and refresh. But Google **refresh tokens** themselves expire: if the OAuth app is in "Testing" publishing status, refresh tokens die after **7 days**; refresh tokens also expire after 6 months of non-use, and there's a per-client/per-user token-count cap that silently invalidates the oldest tokens. None of this is in FR-18. Combined with H1 (app stuck in Testing because unverified), every operator silently disconnects weekly. This is a correctness time-bomb, not an edge case.

### H4. [high] "Multi-operator token collisions" / re-connect overwrite semantics undefined. (FR-18, §6)
`calendar_operator_tokens` is keyed "by project + operator." What happens when the same operator connects twice (re-consent)? Upsert/overwrite? What if an operator belongs to two projects — two rows, two independent tokens, or shared? What if two Telegram operators legitimately share one Google account? The composite-key behavior on conflict is a data-integrity requirement and it's absent.

### H5. [high] FR-21's "no measurable latency when disabled" is asserted as a guarantee but the pipeline-placement decision that determines it is left open. (FR-21, project-context L73)
FR-21 acceptance: "calendar logic adds **no measurable latency** (config check precedes any intent detection or API call)." Meanwhile project-context L73 explicitly says the architect must *still decide* whether calendar is a standalone answerer or a `scheduling_context` signal. Those two placements have **different** latency/ordering properties, yet the PRD already promises the outcome. You cannot guarantee "config check precedes intent detection" as an acceptance criterion while the placement that governs ordering is undecided. Either the placement is decided (and stated) or the guarantee is premature. Also "no measurable latency" needs a number to be testable (a per-project config read is a SQLite hit — is that "no measurable latency"? Is the project-settings read cached?).

### H6. [high] The OAuth callback's public exposure and routing home are unresolved, with a real attack surface. (FR-18, project-context L76)
Google's redirect target must be a publicly reachable HTTPS URL. project-context L76 says "decide its home (api vs web_ui) in the architecture step" — fine — but the PRD imposes **no requirement** on: rate-limiting the callback (it's an unauthenticated browser endpoint that triggers token exchange), what it renders to the browser on success/failure (HTML? redirect? the operator is in a browser, not Telegram), and how the operator's Telegram identity is bound to a browser session that has no Telegram cookie. FR-18 says the callback validates "that the authenticated operator matches the connect request" — **but the browser hitting the callback is not authenticated as a Telegram user.** The only binding is `state`. So the real CSRF/identity guarantee rests entirely on `state` (see H2), and FR-18's wording ("the authenticated operator") overstates what's actually possible.

---

## MEDIUM

### M1. [medium] Free/busy staleness / caching window unspecified. (FR-19, project-context L134)
project-context L134 says "cache the **access token** until near-expiry" — that's the token, not the freeBusy *result*. But the review brief flags stale free/busy caching as a live risk, and rightly: if the system caches freeBusy responses to cut API calls, a slot that just got booked still reads "free." FR-19 has no requirement on freeBusy result freshness/TTL. If it does NOT cache, that should be stated too (one live call per question). Either way it's a missing correctness requirement, especially given the C7 zero-wrong-answer target.

### M2. [medium] "Working hours / service-days / dates" granularity is vague and unbounded. (FR-20)
FR-20: "which **days/dates** the service runs." Days-of-week (recurring) and specific dates (exceptions/holidays) are different data shapes. Does it support holiday closures? Per-date overrides? Multiple working-hour windows per day (lunch break)? The existing system already pulls RU holidays via the `holidays` lib — does service availability respect them? Unspecified granularity = the data model gets locked too narrow or too wide. Acceptance criterion "honor the configured duration, working hours, and service-days" doesn't mention dates/exceptions despite the FR text introducing "dates."

### M3. [medium] "Duration" semantics undefined for an availability question. (FR-19, FR-20)
A service has a "duration." The customer asks "is X free at 15:00?" To answer, you need a free *block* of `duration` length starting at 15:00. Does the customer specify a start time, and the system checks `[start, start+duration)`? Or a window? FR-19 says "compute candidate availability … over the relevant window" — "relevant window" is undefined. Without stating the slot-fitting rule, FR-19's acceptance ("a slot that is free AND satisfies all rules is available") can't be implemented deterministically.

### M4. [medium] Acceptance criteria contain vague/soft language and an undefined fallback string. (FR-19, FR-21)
FR-19 AC: "Provider/token failures produce an escalation (**or** a safe 'let me check and get back to you')". The "or" makes the behavior non-deterministic — which one, when? A test can't assert "or." Pick one per branch. Also "safe 'let me check and get back to you'" is a customer-facing Russian string that, per the repo convention (project-context L95, "Russian-first content is DATA"), must be a configurable `data/*` entry — the PRD floats it as inline English prose with no home. FR-21 uses "**helpful** 'calendar isn't connected yet' path **and/or** HITL" — same "and/or" non-determinism.

### M5. [medium] "Escalate on uncertainty" interaction with existing HITL is asserted, not specified. (FR-19, FR-3)
FR-19 escalates "to HITL rather than guessing." But the existing HITL ticket (FR-3) creates an operator-routed ticket with a verbatim customer question. When a *calendar* question escalates, does the operator see "customer asked about availability, calendar errored" context, or just the raw question? Does the escalation route to the **connected calendar operator** specifically (they own the calendar!) or to the project's `hitl_primary_operator`? This routing question is unstated and interacts directly with C1. A calendar escalation landing on someone with no visibility into the calendar is a dead end.

### M6. [medium] §7 / FR-7 (Alerts UI) got no calendar additions, contradicting the "every epic integrates with incidents" rule. (§7, project-context L105)
project-context L105: "From Epic 03 onward, every epic integrates with the incident/alerts solution (Epic 02)." The calendar feature introduces brand-new failure classes (OAuth exchange failure, token refresh failure, freeBusy provider errors, mass token expiry per H3). FR-8's incident type list (line 129) was **not** extended with calendar/OAuth failure types, and §7 has no calendar mention. Either the rule is being violated or the integration is implicit — either way the PRD is silent where its own grounding says it must not be.

---

## LOW

### L1. [low] Disconnect does not specify Google-side revocation vs local delete. (FR-18)
FR-18: "The operator can disconnect (**revoke** + delete stored token)." "Revoke" implies calling Google's token-revocation endpoint. Is that mandatory (best practice — kills standing access immediately) or is local delete sufficient? If the revoke call fails but local delete succeeds, what's the state? Minor, but it affects the NFR-3 "leaked token = standing access" mitigation story.

### L2. [low] §2.2 read-only non-goal is well-fenced, but FR-20's "bookable services" wording leaks write-intent vocabulary. (§2.2, FR-20)
§2.2 cleanly defers booking. But FR-20 line 274 says each project "defines its **bookable** services." "Bookable" implies booking — the very thing that's out of scope. Read-only availability would more accurately call them "schedulable" or "offered" services. Minor wording, but in a brownfield doc the term "bookable" will mislead the next reader into thinking write is in scope (and seeds the scope-creep the non-goal is trying to prevent).

### L3. [low] Success metric "availability-answer rate vs escalation" lacks a target/baseline. (§8 success metrics)
The counter-metric (incorrect availability ≈ 0) is well-defined. But "availability-answer rate vs escalation" has no target — is 50% escalation a success or a failure for a calendar-enabled project? Without a number it's a dashboard line, not a release gate. Lower severity because the *correctness* counter-metric is the one that matters.

### L4. [low] No requirement on what happens when a connected operator has multiple Google calendars. (FR-18, FR-19)
A Google account commonly has several calendars (personal, work, holidays). freeBusy can query specific calendar IDs. FR-18 stores "their … Google Calendar" (singular) but a connected account exposes many. Which calendar(s) define "busy"? Primary only? All? Operator-selected? Unspecified — defaults to "primary" by accident, which may be the wrong calendar for their work bookings.

---

## Counts by severity
- critical: 4 (C1–C4)
- high: 6 (H1–H6)
- medium: 6 (M1–M6)
- low: 4 (L1–L4)
- **Total: 20**

## Bottom line
The security/crypto posture (encryption-at-rest, read-only scope, no-logging) is solid in prose. The *product* spec is not: whose calendar (C1), which service (C2), and whose timezone (C3) are the three load-bearing questions an architect must answer before touching the data model — and the PRD answers none of them. Add to that the unaddressed Google OAuth verification dependency (H1) and refresh-token expiry reality (H3), which are external/operational time-bombs that "read-only first" framing conveniently hides. Resolve C1–C4 and H1/H3 before drafting epic-11 stories.
