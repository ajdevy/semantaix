# Epic 07 Story 01 — Backup/Restore Runbook

## Scope
Operational hardening for the Semantaix data plane. Backups archive every
SQLite database listed in `BACKUP_SOURCE_PATHS`; restores extract the archive
back into a chosen target directory.

## Endpoints
- `POST /api/backups/run` — runs a backup and returns the metadata row.
- `GET /api/backups` — lists backups, most recent first.
- `GET /api/backups/last-successful` — returns the latest successful backup
  (or `{ "backup": null }` when none).
- `GET /api/backups/{id}` — fetches a specific record (404 if missing).
- `POST /api/backups/{id}/restore` — restores an archive after token check.
  Body must contain:
  - `confirm_token`: literal string `restore-<id>` (any other value returns 400).
  - `target_root`: directory to extract files into; directory is created if
    missing.

## Web UI
- `/admin/backups` (served via `web_ui` behind nginx) shows the most recent
  successful backup’s id, completion time, archive path, and size, plus the
  curl recipe for invoking restore safely.

## Failure Modes & Alerts
- Backup failures emit incident `backup_failures` (severity `critical`) via
  `IncidentRepository.ingest`.
- Restore failures (other than the token guard) emit
  `backup_restore_failures` and surface in the alerts feed.
- The repository persists `backup_failed` and `restore_failed` rows in
  `backup_events` for forensic context.

## Manual Verification Steps
1. `curl -X POST http://localhost/api/backups/run` — confirm `status: success`
   and a non-zero `size_bytes`.
2. `curl http://localhost/api/backups/last-successful` — confirm the same id is
   surfaced.
3. Open `http://localhost/admin/backups` — confirm last-backup card shows the
   archive path and size.
4. Restore into a sandbox directory:
   ```sh
   curl -X POST http://localhost/api/backups/<id>/restore \
     -H 'content-type: application/json' \
     -d '{"confirm_token":"restore-<id>","target_root":".tmp/restored"}'
   ```
   Verify the SQLite files appear under `.tmp/restored/`.
5. Negative checks:
   - `confirm_token=wrong` returns 400.
   - Restoring a non-existent id returns 404.
   - Pointing at an archive deleted from disk returns 500 and emits a
     `backup_restore_failures` incident.

## Scheduling
Runs are triggered today by hitting `POST /api/backups/run`. Scheduling is
provided externally (cron in the host or a future scheduler-service tick) —
the API itself is idempotent and safe to call repeatedly. Each invocation
creates a new archive with a unique filename suffix.

## Related Settings
- `BACKUP_DB_PATH` — metadata SQLite file (default `.data/semantaix_backups.db`).
- `BACKUP_ARCHIVE_DIR` — directory for `*.tar.gz` archives.
- `BACKUP_SOURCE_PATHS` — comma-separated list of files to include. Missing
  files are skipped silently so a fresh install can still produce an archive.
