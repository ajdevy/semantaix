# Epic 07: Backup/Restore and Operational Hardening

## Goal
Add recovery controls and final reliability hardening for production readiness.

## In Scope
- Backups of the SQLite system of record as **tar.gz archives** (the original Qdrant-snapshot scope was superseded since live data lives in SQLite). Runs are triggered via the API / Web UI; the `scheduler` service remains a heartbeat placeholder.
- Backup metadata tracking (timestamp, archive path, status) in `semantaix_backups.db` (`backups` + `backup_events`)
- Restore flow via Web UI with a confirmation-token safety control
- Runbooks and E2E reliability suite
- Incident emission integration into Epic 02 backbone

## Out of Scope
- New feature workflows outside hardening/recoverability

## Exit Criteria
- Backup run verified (on-demand trigger produces a tar.gz archive + metadata)
- Restore validated on test dataset (with confirmation token)
- Last backup timestamp/archive path visible in UI
- Backup/restore failures appear in Alerts flow

## Automated E2E verification

Implemented (`services/api/app/backups.py`, Web UI `/backups`). Covered by
`tests/e2e/test_e2e_epic07_backup_restore.py::test_epic07_backup_run_then_restore`
(backup run → list → restore round-trip), plus repository/contract/UI unit tests
(`tests/test_backups_repository.py`, `tests/test_api_backups_contract.py`,
`tests/test_web_ui_backups.py`). See `_bmad-output/implementation-artifacts/e2e-coverage.md`.
