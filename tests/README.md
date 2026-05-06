# Tests

- **Contract & unit modules** live at the package root (`test_*`).
- **Story E2E** modules live under **`e2e/`** and use **`@pytest.mark.e2e`**.
- **`pytest`** from the repo root runs everything CI runs (except install).
- **`pytest -m e2e`** selects the story-oriented subset only.

Canonical mapping table: [_bmad-output/implementation-artifacts/e2e-coverage.md](../_bmad-output/implementation-artifacts/e2e-coverage.md).
