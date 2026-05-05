# Semantaix Implementation Gate Policy

## Purpose
Enforce strict feature-by-feature implementation order.

## Rules
1. Only one epic can be `in_progress` at a time.
2. No story from a later epic may be implemented before current epic signoff.
3. A current epic can close only when all stories pass:
   - unit/integration/UI tests
   - manual verification checklist
   - acceptance demo
4. Epic 02 (incident/alerts foundation) is mandatory dependency for Epic 03+.
5. From Epic 03 onward, each epic must include:
   - incident emission for new failure modes
   - alert visibility in Alerts UI
   - manual alert verification case

## Signoff Checklist (per epic)
- [ ] Story acceptance criteria complete
- [ ] Automated tests passing
- [ ] Regression checks complete
- [ ] Manual verification completed
- [ ] Product/owner signoff recorded
