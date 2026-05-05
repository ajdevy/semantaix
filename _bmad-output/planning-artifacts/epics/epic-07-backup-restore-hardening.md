# Epic 07: Backup/Restore and Operational Hardening

## Goal
Add recovery controls and final reliability hardening for production readiness.

## In Scope
- Scheduled Qdrant backups
- Backup metadata tracking (timestamp, location, status)
- Restore flow via Web UI with safety controls
- Runbooks and E2E reliability suite
- Incident emission integration into Epic 02 backbone

## Out of Scope
- New feature workflows outside hardening/recoverability

## Exit Criteria
- Backup schedule verified
- Restore validated on test dataset
- Last backup timestamp/location visible in UI
- Backup/restore failures appear in Alerts flow
