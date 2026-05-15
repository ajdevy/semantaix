# Story 10.07 — Multi-operator resolution in bot_gateway + inbound routing

## Objective
Replace the implicit "single primary operator" assumption in `bot_gateway` with a registry lookup against the `operators` table from 10.01, while preserving full backwards compatibility for the existing `settings.hitl_primary_operator_username` flow. The primary operator remains the default fallback; new operators added via the admin surface are recognized for `/files`, `/send`, `/kb_*`, and HITL assignment.

## Scope

### In Scope
- `services/bot_gateway/app/main.py`:
  - Replace direct `username == _effective_operator_username()` checks with `await _resolve_operator_for_sender(username)` returning `Operator | None`.
  - `_resolve_operator_for_sender`:
    1. Calls api `GET /operators/by-username/{u}` (10.03 endpoint), authenticated via `X-Internal-Token`.
    2. If 200 and `is_active` → return the operator.
    3. If 404 or `is_active=false` → return `None`.
    4. On HTTP failure → log and fall back to `settings.hitl_primary_operator_username` comparison (bootstrap-window safety).
  - Operator-only command sites updated to use this helper.
- `services/api/app/main.py` `/conversations/inbound`:
  - HITL routing now assigns the ticket to the operator bound to the **default project** unless the customer is already in an open ticket. Future stories may refine per-project routing; for 10.07 the assignment policy is unchanged for the primary operator and explicit for newly-registered operators only when the customer's existing ticket points to them.
  - When creating a *new* HITL ticket and `customer_username` already has a recent answered conversation, the previously-assigned operator (if active) is preferred; otherwise the primary operator is assigned. Single-operator deployments behave identically to today.
- `services/api/app/hitl.py` (only if absolutely required):
  - Add a thin `latest_assigned_operator(customer_username) -> str | None` helper so `/conversations/inbound` can pick "sticky" routing without leaking SQL.

### Out of Scope
- Per-project customer routing rules (out of scope until business decides on rules).
- Operator workload balancing.
- Operator availability / on-call schedules.

## Implementation Notes
- `OperatorRepository.find_by_username` is the source of truth.
- Bot-side caching: cache the last successful lookup per `(username, project_id)` for 30 seconds to avoid hot-path latency. Keep the cache tiny (LRU 64).
- The fallback path is critical: if api is briefly unreachable, the primary operator's commands must keep working. Tests must cover this branch explicitly.

## Test Plan

### Unit
- `tests/test_bot_gateway_operator_resolution.py` — registered operator resolves; unknown returns None; HTTP failure falls back to primary operator (matches → returns synthesized `Operator` with project_id from default); inactive operator returns None.

### API contract
- Re-use `tests/test_api_operators_contract.py` from 10.03 (`GET /operators/by-username/{u}`).

### Integration
- `tests/test_api_inbound_sticky_operator_routing.py` — customer with prior ticket assigned to operator-B gets the next ticket assigned to operator-B (when still active); otherwise primary operator. Single-operator setup with empty `operators` table still routes via primary.

## Automated E2E verification
- Combined with `tests/e2e/test_e2e_epic10_rag_scope.py` from 10.06 (operator-B's customer ends up scoped to project B because routing now flows through operator-B's project_id).

## Manual Verification
1. With only primary operator registered, send a customer message → behavior unchanged from Epic 04.
2. Add `@op-b` (chat_id present) via admin surface, route the next customer to `@op-b` via direct HITL reassign (`/hitl/tickets/{id}/route?operator_username=@op-b`) — confirm `@op-b` receives the operator DM and can `/files`, `/send`.
3. Stop api briefly; confirm `@op-b`'s `/files` fails gracefully ("ошибка соединения с api") AND that primary operator's `/files` still works (because fallback kicks in on the primary's username).

## Done Criteria
- All unit + contract + integration + e2e tests pass.
- 100% coverage on `_resolve_operator_for_sender` and any new HITL helpers.
- `ruff check .` passes.
- A clean stack with only the primary operator behaves identically to pre-Epic-10 builds (no regression).
