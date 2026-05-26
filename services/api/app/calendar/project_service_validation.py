"""Validate a ``project_services`` write before it reaches the repository
(Epic 13, story 13.02).

The canonical surface accepts the full FR-23 service shape: ``name`` (required),
optional catalog metadata (``description``, ``price_text``, ``tags``), and
optional calendar-eligibility fields (``duration_minutes``, ``working_hours``,
``service_days``, ``date_exceptions``). The calendar-shape rules already live in
:mod:`services.api.app.calendar.service_rule_validation` (story 11.08); this
helper extends them with the new mandatory-``name`` check.

Raises :class:`ProjectServiceValidationError` with a stable ``reason`` code
caught at the HTTP boundary and translated to a 400 response.
"""

from __future__ import annotations

from services.api.app.calendar.service_rule_validation import (
    ServiceRuleValidationError,
    validate_service_rule,
)


class ProjectServiceValidationError(ValueError):
    """A canonical project-service payload is malformed.

    ``reason`` is one of: ``invalid_service_name``, ``invalid_duration``,
    ``invalid_working_hours``, ``invalid_service_days``,
    ``invalid_date_exceptions``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_project_service(
    *,
    name: object,
    duration_minutes: int | None,
    working_hours: object,
    service_days: object,
    date_exceptions: object,
) -> str:
    """Validate the full canonical payload; return the whitespace-stripped name.

    Reuses :func:`validate_service_rule` for the four calendar-shape fields and
    adds a mandatory-non-empty-``name`` check (FR-23: every catalog row has a
    label).
    """
    if not isinstance(name, str):
        raise ProjectServiceValidationError("invalid_service_name")
    stripped = name.strip()
    if not stripped:
        raise ProjectServiceValidationError("invalid_service_name")
    try:
        validate_service_rule(
            duration_minutes=duration_minutes,
            working_hours=working_hours,
            service_days=service_days,
            date_exceptions=date_exceptions,
        )
    except ServiceRuleValidationError as exc:
        raise ProjectServiceValidationError(exc.reason) from exc
    return stripped
