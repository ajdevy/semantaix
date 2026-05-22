# PRD Quality Review — Semantaix PRD

## Overall verdict

This is a disciplined, capability-spec brownfield PRD whose existing body is decision-ready and whose new Calendar feature group (FR-18–FR-21) is unusually strong on the safety thesis — escalate-on-uncertainty, read-only scope, encrypted token storage, default-off gating — and faithfully mirrors the decision log. What's at risk is a small cluster of done-ness gaps in the new FRs: the PRD never says *which* operator's calendar answers a question when a project has several connected (FR-19/FR-20 "where applicable"), where the project timezone comes from for FR-20's working-hours math, and whether the English "calendar isn't connected yet" string (FR-21) is literal customer-facing copy when answers are otherwise Russian. None are conceptual flaws; all are story-blocking ambiguities that an engineer would have to guess on.

## Decision-readiness — strong

The PRD reads as a set of committed decisions, not considerations. The Calendar additions land their trade-offs explicitly: FR-19 states "A wrong 'yes, it's free' is treated as worse than an escalation," and §7 backs it with a counter-metric (incorrect-availability rate, "target ≈ 0"). NFR-3 names the token-storage trade-off in the open — "A leaked refresh token equates to standing calendar access, so encryption-at-rest and read-only scope are mandatory mitigations" — and the decision log records that dedicated-SQLite-Fernet was *chosen over external secret-manager*. The §2.2 reconciliation note honestly retires a stale Non-Goal ("Multi-tenant architecture") rather than pretending it never conflicted. These are real decisions a reviewer could push back on and find acknowledged.

### Findings
- **low** No Open Questions / `[NOTE FOR PM]` at the genuine open tension (§4 FR-19/FR-20) — The multi-operator-per-project resolution (whose calendar is authoritative) is left implicit rather than flagged as an open decision. *Fix:* add a `[NOTE FOR PM]` or Open Question naming the multi-operator selection rule as undecided, so it isn't silently assumed at story time.

## Substance over theater — strong

Little furniture. The four personas (§3) each do work — On-Call Owner (`@ajdevy`) is referenced concretely in FR-4/FR-8; Operator drives FR-3 and now FR-18. NFRs carry product-specific content rather than boilerplate: NFR-3 specifies Fernet/AES, env-sourced keys, and explicit exclusion zones (logs, env, answer-trace metadata) rather than "system must be secure." The Calendar feature group's novelty claim (free/busy ∩ per-service rules) is earned, not decorative. NFR-4 ("latency target and throughput thresholds must be defined") is the one placeholder, but it's honestly marked as TBD rather than dressed up.

## Strategic coherence — strong

The PRD has a clear thesis — grounded answers or escalate, never ship uncertainty (decision-log entries on HITL-as-safety, guardrail engine, append-only traces) — and the Calendar feature is a clean extension of it rather than a bolt-on. FR-19's escalate-on-uncertainty and "never echo event content" are the same safety arc applied to a new data source. The opt-in/default-off framing (FR-21) follows from the thesis that most projects shouldn't pay for capability they don't use. §7 metrics validate the thesis (deflection, groundedness, incorrect-availability counter-metric) rather than vanity activity counts.

## Done-ness clarity — adequate

Most Calendar acceptance criteria are genuinely testable: FR-19's three bullets give a clear truth table (busy OR outside-rules → not available; free AND all-rules-satisfied → available; provider/token failure → escalate, never fabricate), and FR-21's "no measurable latency / config check precedes any intent detection or API call" is verifiable. FR-18's CSRF/identity-mismatch rejection is concrete. But several requirement gaps would force an engineer to guess, and this is the dimension story creation leans on hardest.

### Findings
- **high** Multi-operator-per-project selection undefined (§4 FR-19, FR-20) — FR-19 computes from "an operator's Google Calendar" / "the relevant operator" but never says which operator's calendar is authoritative when a project (built on Epic-10 multi-operator scoping) has several connected. FR-20's "scoped per project (and per operator where applicable)" leaves "where applicable" undefined. An engineer cannot implement availability without this. *Fix:* state the selection rule — e.g. service rules bind a service to a specific operator, or availability unions/intersects all connected operators — and define behavior when zero or multiple match.
- **high** Project timezone source unspecified (§4 FR-20, §8) — §8 says compare in UTC via "config-driven project timezone," but no FR establishes where that timezone is stored or that FR-20 working-hours/service-days are interpreted in it. Working-hours math is meaningless without a defined tz source. *Fix:* add to FR-20 (or `calendar_project_settings`, §6) an explicit project-timezone field and state that working hours/service-days are evaluated in it.
- **medium** Customer-facing wording language ambiguous (§4 FR-21) — FR-19 mandates answers "in Russian," but FR-21(b) quotes "calendar isn't connected yet" and FR-19 quotes "let me check and get back to you" in English. Unclear whether these are literal copy (then they violate the Russian-first rule) or paraphrases. *Fix:* state that all quoted customer-facing strings are illustrative and the actual copy is Russian (and ideally point to where the copy is configured).
- **medium** Externally-revoked-token detection not specified (§4 FR-18, FR-21) — FR-18 handles refresh *failure* → "reconnect" state, and FR-21(b) lists "token revoked," but no acceptance criterion says how a user-side Google revocation is detected or how quickly it surfaces. *Fix:* add a criterion that a revoked/invalid refresh token is detected on next use and transitions the operator to the reconnect state without a customer-visible error.
- **low** "freeBusy window" bound unspecified (§4 FR-19) — "over the relevant window" is undefined (how far forward can a customer ask?). *Fix:* state a max look-ahead horizon or that it's a per-service/per-project config value.
- **low** NFR-4 still a placeholder (§5) — "latency target and throughput thresholds must be defined" gives no number; the Calendar "no measurable latency" claim (FR-21) has no baseline to measure against. *Fix:* set the MVP latency/throughput numbers, or note explicitly that they're deferred and where they'll be fixed.

## Scope honesty — strong

Omissions are explicit. §2.2 now carries the booking/event-creation Non-Goal in bold ("The calendar capability is read-only availability first … creating or modifying calendar events is deferred"), cross-referenced to FR-18–FR-21. The post-MVP reconciliation note distinguishes original-MVP non-goals from shipped-post-MVP scope (Epics 08/10) rather than silently editing history. FR-21's tri-state explicitly enumerates the not-enabled and enabled-but-disconnected cases so they aren't inferred. Open-items density is appropriately low for a green-light-to-build internal PRD. The one gap is the absence of an explicit `[ASSUMPTION]` tag on the multi-operator behavior (see Done-ness), but the PRD does not over-assume elsewhere.

## Downstream usability — adequate

IDs are contiguous and unique (FR-1–FR-21, NFR-1–NFR-7), and the new cross-references resolve internally (FR-19↔FR-20, FR-21↔§2.2, NFR-3↔Epic 11). §6 adds the three calendar tables and §10 maps FR-18–FR-21 to Epic 11 with named dependencies (Epics 01/03/08/10/02/09). The chief downstream risk is the absence of a Glossary: "service" carries two senses in this repo (microservice vs. bookable offering) and the new FRs use the bookable sense without disambiguation; "project," "operator," and "tenant" are used as load-bearing nouns without definitions. This will cost story-creation precision even though intra-PRD usage is mostly consistent.

### Findings
- **medium** No Glossary; "service" overloaded (§4 FR-19/FR-20 vs. §1/architecture) — FR-19/FR-20 use "service" to mean a bookable offering, while the platform elsewhere uses "service" for FastAPI microservices. "project"/"operator"/"tenant" are also undefined load-bearing terms. *Fix:* add a short Glossary defining bookable-service vs. microservice, project, operator, tenant — downstream UX/architecture/story extraction depends on stable nouns.
- **low** Epic 11 file is a forward reference (§10) — `epic-11-calendar-availability-scheduling.md` is marked "to be created"; cross-ref resolves only after `bmad-create-epics-and-stories` runs. Acceptable as a TODO but currently dangling. *Fix:* none required pre-epic-generation; track that it must exist before stories are sourced.

## Shape fit — strong

The PRD correctly inhabits a capability-spec / internal-tool shape: FR-driven, light personas, operational metrics (§7) rather than user-journey ceremony — appropriate for a single-operator-role brownfield tool, and the rubric explicitly says UJs may be overhead here. The brownfield handling is honest: §2.2's reconciliation note and §10's "builds on … shipped post-MVP (Epics 08/10)" accurately distinguish delivered scope from new work, and the Calendar group is positioned as Epic 11 building on existing pipeline/scoping/incident/command surfaces. No over-formalization (no forced UJ density) and no under-formalization. The one shape-consistency nit: the existing FR-15 header still says "Tenant-Scoped" while §2.2 now treats project-scoping as the live model — minor terminology lag, not a shape problem.

## Mechanical notes

- **Glossary:** absent. Recommended (see Downstream usability) — "service" overload is the sharpest case; "project"/"tenant"/"operator" undefined.
- **ID continuity:** FR-1–FR-21 contiguous and unique; NFR-1–NFR-7 contiguous. No gaps or duplicates. Cross-references (FR-19↔FR-20, FR-21↔§2.2, NFR-3 Epic 11, §6 tables ↔ FRs, §10 epic map) all resolve internally.
- **Assumptions Index:** no inline `[ASSUMPTION]` tags exist and no index — consistent (nothing to round-trip), but the multi-operator behavior arguably warrants one.
- **Decision-log alignment:** the new FR-18–FR-21, NFR-3 extension, §2.2 reconciliation, §6 tables, §7 metrics, §8 risks, and §10 mapping all match the 2026-05-22 session entries. No conflict with bootstrapped decisions — the multi-tenant Non-Goal supersession is reconciled in both the PRD note and log rather than contradicted.
- **Terminology lag:** FR-15 header "Tenant-Scoped" predates the §2.2 project-scoping reconciliation; cosmetic, worth aligning when convenient.
- **Forward ref:** `epic-11-calendar-availability-scheduling.md` (§10) not yet created (expected).
