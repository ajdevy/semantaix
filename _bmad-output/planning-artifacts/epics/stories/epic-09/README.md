# Epic 09 Story Pack

Epic: Operator-Driven KB Growth via Telegram

This story pack is implementation-ready and includes:
- scope boundaries
- test requirements (unit/integration)
- manual verification steps
- completion gates

Implementation order follows story numbering (09.01 → 09.05).

## Automated E2E (current repo)

Story-aligned E2E tests land in `tests/e2e/test_e2e_epic09_*.py` (`@pytest.mark.e2e`). The earliest E2E test belongs to story 09.04 (api endpoint round-trip); the full bot-to-answer flow lands in story 09.05. CI runs `pytest -m e2e` plus the standard `pytest` with coverage. Story-level rows live in `_bmad-output/implementation-artifacts/e2e-coverage.md`.
