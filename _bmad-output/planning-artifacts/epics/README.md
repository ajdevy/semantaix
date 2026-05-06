# Semantaix Epics and Stories (Feature-Sequential)

This directory contains the BMAD feature-based sequential epic layout.

## Hard Rule
- Only one feature epic can be in implementation at a time.
- No feature from later epics may be implemented early.
- Next epic starts only after:
  - story tests pass
  - feature regression check passes
  - demo/acceptance signoff is completed

## Epic Order
1. `epic-01-telegram-llm-suggestions.md`
2. `epic-02-incident-alert-foundation.md`
3. `epic-03-guardrails-validity.md`
4. `epic-04-hitl-escalation.md`
5. `epic-05-rag-foundation.md`
6. `epic-06-knowledge-moderation.md`
7. `epic-07-backup-restore-hardening.md`
8. `epic-08-tenant-knowledge-ops-and-answer-traces.md`

## Recent Implementation Notes
- **Epic 04 (HITL escalation):** runtime HITL recipient/chat routing can be updated by Telegram command `/hitl_config @username <chat_id>`.
- **Access control:** only `HITL_CONFIG_ADMIN_USERNAME` (currently `@ajdevy`) is authorized to apply runtime HITL configuration changes.

## Carry-forward Constraint
From Epic 03 onward, every epic must integrate with the incident/alerts solution from Epic 02.
