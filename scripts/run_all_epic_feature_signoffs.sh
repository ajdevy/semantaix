#!/usr/bin/env bash
# Epic feature signoffs for this repo: CI parity + per-epic live demos.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

VENV_PYTEST="${ROOT_DIR}/.venv/bin/pytest"
VENV_RUFF="${ROOT_DIR}/.venv/bin/ruff"

if [[ ! -x "${VENV_PYTEST}" ]] || [[ ! -x "${VENV_RUFF}" ]]; then
  echo "Missing .venv with dev deps. Run:" >&2
  echo "  python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 127
fi

echo "== ruff (CI parity) =="
"${VENV_RUFF}" check .

echo "== pytest + coverage (CI parity) =="
"${VENV_PYTEST}" --cov --cov-config=.coveragerc --cov-report=term-missing

for epic in 01 02 03 04 05 06 07 08 09; do
  echo "== Epic ${epic} live demo =="
  bash "${ROOT_DIR}/scripts/epic${epic}_signoff_demo.sh"
done

echo "All epic feature signoffs completed OK."
