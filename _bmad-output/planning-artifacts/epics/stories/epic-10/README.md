# Epic 10 Story Pack

Epic: Multi-Operator + Projects + Admin Surface

This story pack is implementation-ready and includes:
- scope boundaries
- test requirements (unit/integration)
- manual verification steps
- completion gates

Implementation order follows the dependency graph below (10.01 blocks every later story; 10.06 is independent of the admin-auth branch):

```
10.01 schemas + bootstrap
  ├── 10.02 admin login-code auth
  │     ├── 10.03 admin web UI pages       (parallel after 10.02)
  │     ├── 10.04 admin Telegram commands  (parallel after 10.02)
  │     └── 10.05 admin NL dialog          (after 10.04)
  └── 10.06 RAG project scoping
        └── 10.07 multi-operator routing in /conversations/inbound
```

## Automated E2E (current repo)

Story-aligned E2E tests land in `tests/e2e/test_e2e_epic10_*.py` (`@pytest.mark.e2e`). The earliest E2E test belongs to story 10.02 (admin login round-trip with mocked DM). The end-to-end project lifecycle test lands in 10.05, and RAG scoping verification in 10.07. CI runs `pytest -m e2e` plus the standard `pytest` with coverage. Story-level rows live in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
