# Implementation Readiness Assessment Report

**Date:** 2026-05-22
**Project:** Semantaix — Epic 11 (Calendar Availability & Scheduling)
**Scope:** PRD FR-18–FR-22 + NFR-3 ext ↔ architecture (Epic 11 section) ↔ epic-11 + stories 11.01–11.07

## Verdict: ✅ READY (both conditions resolved 2026-05-23)

The plan is coherent and traceable: every FR-18–FR-22 acceptance criterion maps to at least one story, the dependency order is sound, deferred items are consistent across all four docs, and no requirement is contradicted. The two gaps below have been **resolved** — epic-11 is green for Phase 4.

**Resolution summary:**
- **Finding 1 → resolved** by adding **story 11.08** (enable/disable + service-config surface) with a permission model: operator **and** admin can enable/disable; **only the operator** may disconnect/delete (admin cannot delete). PRD FR-18/FR-21 updated accordingly.
- **Finding 2 → resolved** by tightening story 11.07 to require exactly **one clarifying turn** (matching FR-22); the hedge was removed.

## Requirements → Story coverage

| Requirement | Covered by | Status |
|---|---|---|
| **FR-18** OAuth connect (consent, single-use `state`, callback, encrypted token, re-consent overwrite, revocation/expiry → reconnect+notify+incident+clear, disconnect) | 11.01 (token+state repos), 11.02 (consent/callback/exchange/store/disconnect), 11.03 (Telegram cmd), 11.04 (refresh + revocation/expiry handling) | ✅ Full |
| **FR-19** Availability answering (freeBusy ∩ rules, project tz, never echo events, escalate-on-uncertainty) | 11.04 (freeBusy client), 11.05 (compute_availability), 11.07 (orchestration + escalate routing) | ✅ Full |
| **FR-20** Per-service rules (duration, working-hours windows, service-days, date exceptions/RU holidays) | 11.01 (schema), 11.05 (parse + apply), **11.08 (config surface)** | ✅ Full (resolved) |
| **FR-21** Opt-in gating (default-off, tri-state, cheap gate before intent/API) | 11.01 (settings, default-off), 11.07 (tri-state gate + ordering), **11.08 (enable/disable, operator+admin)** | ✅ Full (resolved) |
| **FR-22** Russian service resolution (lemma match → clarify-once → escalate) | 11.06 (resolver + time extract), 11.07 (one-turn clarify, hedge removed) | ✅ Full (resolved) |
| **NFR-3** OAuth/token security (Fernet at rest, key/secret in env, never logged) | 11.01 (encryption), 11.02/11.04 (never logged), done-criteria log-capture tests | ✅ Full |

## Findings

### Finding 1 (HIGH — scope decision) — No configuration entry point for enable/designate/service-rules
The repos in 11.01 expose `enable(project_id, calendar_operator, tz, lookahead)`, `set_calendar_operator`, and service-rule CRUD — but **no story delivers a user-facing way to invoke them.** FR-20/FR-21 imply calendar is configurable; the epic Out-of-Scope correctly defers a *web admin UI*, but doesn't state how an admin enables a project / designates the calendar operator / defines services in v1.
- **Impact:** an operator can `/connect_calendar`, but nothing turns the feature on or defines "маникюр (60 min, Mon–Sat 10:00–19:00)". Without a decision, 11.07's "connected" path can't be exercised in production.
- **Options:** (a) **v1 = out-of-band config** — an admin seeds `calendar_project_settings` + `calendar_service_rules` directly via the repo / a one-off script (acceptable for a pilot; document it explicitly in the epic); or (b) **add a small story 11.08** — admin Telegram commands / NL to enable + define services, mirroring epic-10's admin surface (more scope, more complete).
- **Recommendation:** pick (a) for the pilot and add one sentence to the epic + story 11.01 ("services & enablement seeded via repo/admin script in v1; editing surface deferred"), OR commit to (b) as story 11.08. Either closes the gap.

### Finding 2 (MEDIUM — doc tightening) — FR-22 mandates one clarifying turn; 11.07 hedges
FR-22 AC: "*exactly one clarifying turn before escalation*." Story 11.07 Implementation Notes say: "if implementing turn state is heavy, escalate immediately for v1." That's a latent contradiction with the firm FR-22 AC.
- **Resolution:** either (a) commit 11.07 to the one-turn clarify (keeping FR-22 as-is — preferred, matches the "don't dead-end the customer" intent), or (b) relax FR-22 to "may clarify once; otherwise escalate." Decide and align both docs.

### Notes (LOW — no action required, track during dev)
- **Russian date/time parsing** (11.06 `extract_requested_start`) is real NLP effort; FR-19 assumes a resolved start time. The conservative "return None → clarify/escalate" design is correct; just don't under-budget it.
- **Calendar incident types** (11.04) ride the generic fingerprint-based Epic-02 engine; FR-8's explicit incident-type list wasn't extended, which is fine given the engine is generic — but confirm calendar incidents surface in the Alerts UI filters.
- **One-PR-per-story** holds; 11.05 and 11.06 are correctly parallelizable after 11.01.

## Alignment checks
- **Dependency order:** 11.01 → 11.02 → {11.03, 11.04}; 11.05 ∥ 11.06 after 11.01; 11.07 after 11.04/11.05/11.06. Sound; no cycles; 11.01 correctly gates all.
- **Deferred items consistent across PRD / architecture / epic / decision-log:** multi-operator selection, multi-calendar (primary only), booking/write, freeBusy result caching — all four docs agree. ✅
- **No contradictions:** booking non-goal (§2.2) vs "schedulable services" (FR-20) reconciled; project-tz rule consistent; "calendar operator" defined in Glossary (§11). ✅
- **No orphan requirements:** every FR maps to ≥1 story; no story invents scope absent from the PRD/architecture.

## Gate
✅ **Cleared.** Finding 1 resolved (story 11.08 + permission model in FR-18/FR-21); Finding 2 resolved (11.07 committed to one clarifying turn). Epic-11 is now an **8-story** pack and green for Phase 4 (`bmad-sprint-planning` → 11.01).
