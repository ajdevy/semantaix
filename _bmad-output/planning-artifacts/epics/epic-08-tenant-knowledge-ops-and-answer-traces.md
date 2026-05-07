# Epic 08: Tenant Knowledge Operations, Answer Transparency, and Correction Loop

## Goal
Enable the paying **client** (tenant) to maintain knowledge through natural-language interaction, inspect **why** a particular end-customer answer was produced, and **correct** underlying knowledge so future answers change—with safe confirmation, versioning, and audit trails aligned to Epic 06 moderation where the product requires a gate.

## In Scope
- **Answer trace records** (MVP slice): persist a structured, queryable snapshot per delivered (or blocked) bot answer—retrieved chunk references and scores, policy/guardrail summary, model routing metadata, confidence/grounding flags, and explicit limitations—consistent with Option B (FastAPI, PostgreSQL, Qdrant) and **Epic 05** retrieval + **Epic 03** guardrail contracts.
- **Transparency UI**: Web UI surfaces to open a conversation/message and view the trace (sources, policy outcome, routing); read-only for operators as designed.
- **Natural-language knowledge ops**: tenant-authenticated flow (primary: **bot** conversational commands with confirmation steps; optional parity in Web UI) to add/update/deprecate tenant-scoped knowledge; operations create **knowledge versions** and enqueue or complete reindex per approved path.
- **Correction from trace**: from a specific answer trace, jump to “propose fix” (pre-filled draft), optional **moderation** handoff per **Epic 06** when enabled for the tenant, and durable **audit_logs** for all mutations.
- **Incident emission** integration into **Epic 02** backbone for trace persistence failures, NL-op parse failures, and reindex errors.

## Out of Scope
- Re-implementing core RAG (**Epic 05**), guardrails (**Epic 03**), or baseline moderation (**Epic 06**).
- Full multi-tenant billing, org hierarchy, or enterprise RBAC beyond **practical tenant isolation** for knowledge + traces.
- Consumer-grade “explain like I’m five” narratives; MVP is **structured transparency** plus short human-readable summaries.

## Dependencies
- **Epic 05** (retrieval + ingestion identifiers for chunk lineage).
- **Epic 03** (guardrail/validity decision payload to store on the trace).
- **Epic 06** (optional moderation gate and reindex-on-approve behaviors for NL-driven changes).
- **Epic 02** (alerts for operational failures).
- **Epic 07** (operational posture; no new backup semantics required for this epic).

## Exit Criteria
- Every production answer path can be associated with an **answer_trace** record (or explicit “trace unavailable” incident) when guardrails allow delivery or escalation boundaries are met.
- Tenant users can complete at least one **NL knowledge update** with confirmation and see it reflected in retrieval after reindex.
- From the Web UI, a moderator can open **why this answer** for a message and see sources + policy + routing fields defined in the story pack.
- Corrections initiated from a trace write **audit_logs** and respect Epic 06 moderation when the flag is on.
- Trace/NL-op/reindex failures surface in **Alerts**.

## Automated E2E verification

Trace persistence, NL knowledge ops, and moderation-linked correction workflows are **not implemented** in codebase yet relative to this epic’s full scope.

Until those APIs ship, CI only covers a **minimal HTTP smoke** for the static admin shell: **`tests/e2e/test_e2e_epic08_web_ui_smoke.py::test_epic08_admin_shell_reachable`**. Detailed behavior per story is tracked in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
