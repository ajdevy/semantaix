"""Central operator-vs-admin authorization for calendar config (Epic 11, story 11.08).

PRD FR-18/FR-21 split the calendar surface two ways:

- **Enable / disable / configure services** — allowed for *both* the project's
  designated calendar operator *and* an admin. Disable keeps the stored token; it
  only flips ``enabled=0``.
- **Disconnect / delete the integration** — operator-only. An admin attempting it
  is rejected (403).

This module is the single place that decision lives, so every route and every
caller enforces the same rule rather than re-deriving it. The functions raise
``HTTPException`` at the HTTP boundary (per project-context's failure convention).
"""

from __future__ import annotations

from fastapi import HTTPException

from services.api.app.calendar.settings_repository import CalendarProjectSettings

# Roles the bot_gateway stamps on a config call. The operator role is the
# project's calendar operator; the admin role is the configured admin username.
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
_ALLOWED_ROLES = {ROLE_OPERATOR, ROLE_ADMIN}


def _require_known_role(actor_role: str) -> None:
    if actor_role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="unknown_actor_role")


def authorize_calendar_config(
    *,
    actor: str,
    actor_role: str,
    project_settings: CalendarProjectSettings | None,
) -> None:
    """Authorize an enable/disable/service-config write.

    Admins are always allowed. Operators are allowed when they are (or are about
    to become) the project's designated calendar operator: a project with no
    settings row yet — or one whose ``calendar_operator`` matches ``actor`` — is
    fine; an operator who is *not* the designated one is rejected with 403.
    """
    _require_known_role(actor_role)
    if actor_role == ROLE_ADMIN:
        return
    if project_settings is None or project_settings.calendar_operator is None:
        return
    if project_settings.calendar_operator != actor:
        raise HTTPException(status_code=403, detail="not_calendar_operator")


def authorize_calendar_disconnect(*, actor_role: str) -> None:
    """Authorize a disconnect/delete — operator-only; admin → 403."""
    _require_known_role(actor_role)
    if actor_role == ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin_cannot_disconnect")


def authorize_service_remove(*, actor_role: str) -> None:
    """Authorize a destructive service-row removal (Epic 12, story 12.02).

    Mirrors :func:`authorize_calendar_disconnect`: operators may delete catalog
    rows, an admin (even one who is also a registered project operator) is
    rejected with 403 ``admin_cannot_remove_service`` per FR-18/FR-21's
    destructive-op rule. Unknown roles → 400 ``unknown_actor_role``.
    """
    if actor_role == ROLE_OPERATOR:
        return
    if actor_role == ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin_cannot_remove_service")
    raise HTTPException(status_code=400, detail="unknown_actor_role")
