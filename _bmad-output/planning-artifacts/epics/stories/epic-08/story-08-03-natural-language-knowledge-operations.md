# Story 08.03 — Natural-Language Knowledge Operations (Bot-First, Confirmed)

## Objective
Let the **tenant** add, update, or retire knowledge by **talking with the bot** (or equivalent authenticated NL channel), with **preview → confirm → version → reindex** semantics and full audit.

## Scope
### In Scope
- Tenant-authenticated session binding (mechanism per platform: e.g. allowlisted admin Telegram user ids mapped to `tenant_id`, or signed deep link—final choice in implementation plan).
- NL intent pipeline: parse user utterance into structured op (`create`, `update`, `deprecate`, `clarify`) with **slot filling** and **clarifying questions** when ambiguous.
- **Preview** step: show diff summary (text, tags, effective dates if any) before commit.
- **Confirmation**: explicit yes/no (or button callback) required for mutating ops.
- Persist **`knowledge_versions`** / tenant-scoped knowledge rows; enqueue reindex job consistent with **Epic 05** ingestion.
- **`nl_audit_logs`** entries for every committed op and for abandoned previews.
- Feature flag to disable NL ops in production per tenant.

### Out of Scope
- Arbitrary free-form SQL or destructive bulk deletes without confirmation
- Full LLM-based authorization (use explicit allowlists + tenant binding)

## Implementation Notes
- Prefer **idempotent** op tokens for confirm callbacks.
- Rate-limit NL ops per tenant; emit **Epic 02** incident on repeated parse failures.
- When **Epic 06** moderation is enabled for the tenant, NL commits create **candidates** instead of immediate publish (configurable); default for production tenants with strict quality bar.

## Test Plan
### Unit
- intent parsing table tests (golden utterances)
- confirmation token validation

### Integration
- bot flow: utterance → preview → confirm → DB row + enqueue stub
- moderation branch: confirm → candidate row not in index until approve

### UI
- optional: minimal Web UI mirror not required in MVP if bot path is complete

## Automated E2E verification

**Deferred** — NL ops pipeline (`preview → confirm → version → reindex`) is out of scope for current services. Existing Epic 06 E2E covers **moderated knowledge** via API (`tests/e2e/test_e2e_epic06_knowledge_pipeline.py`), not tenant NL conversational ops.

## Manual Verification
1. As tenant admin, issue NL “add FAQ about returns.”
2. Confirm preview; verify version row and audit log.
3. Verify retrieval returns new content after reindex (depends on Epic 05 worker).

## Done Criteria
- Happy path + clarify path + cancel path covered by tests
- Audit trail complete
- Alerts on systematic failures
