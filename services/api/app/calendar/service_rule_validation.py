"""Validate a service-rule write before it reaches the repository (story 11.08).

The shapes must match what ``availability.parse_service_rule`` (story 11.05)
later parses: a ``working_hours`` map of weekday-token → window(s), a
``service_days`` list of weekday tokens, ``date_exceptions`` as ISO dates, and a
positive ``duration_minutes``. We reject malformed input *here*, at write time,
with a clear reason rather than letting a bad row silently break availability.

Raises :class:`ServiceRuleValidationError` (caught at the HTTP boundary and
translated to a 400). Mirrors the engine's tolerant weekday tokens.
"""

from __future__ import annotations

from datetime import date, time

_WEEKDAY_TOKENS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


class ServiceRuleValidationError(ValueError):
    """A service-rule payload is malformed; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _validate_duration(duration_minutes: int | None) -> None:
    if duration_minutes is None:
        return
    if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
        raise ServiceRuleValidationError("invalid_duration")
    if duration_minutes <= 0:
        raise ServiceRuleValidationError("invalid_duration")


def _validate_window_pair(pair: object) -> None:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ServiceRuleValidationError("invalid_working_hours")
    start_raw, end_raw = pair
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        raise ServiceRuleValidationError("invalid_working_hours")
    try:
        start = time.fromisoformat(start_raw)
        end = time.fromisoformat(end_raw)
    except ValueError as exc:
        raise ServiceRuleValidationError("invalid_working_hours") from exc
    if start >= end:
        raise ServiceRuleValidationError("invalid_working_hours")


def _validate_working_hours(working_hours: object) -> None:
    if working_hours is None:
        return
    if not isinstance(working_hours, dict):
        raise ServiceRuleValidationError("invalid_working_hours")
    for token, raw in working_hours.items():
        if not isinstance(token, str) or token.lower() not in _WEEKDAY_TOKENS:
            raise ServiceRuleValidationError("invalid_working_hours")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ServiceRuleValidationError("invalid_working_hours")
        # Flat pair ``["09:00", "18:00"]`` vs list of pairs.
        if isinstance(raw[0], str):
            _validate_window_pair(raw)
        else:
            for pair in raw:
                _validate_window_pair(pair)


def _validate_service_days(service_days: object) -> None:
    if service_days is None:
        return
    if not isinstance(service_days, (list, tuple)):
        raise ServiceRuleValidationError("invalid_service_days")
    for token in service_days:
        if not isinstance(token, str) or token.lower() not in _WEEKDAY_TOKENS:
            raise ServiceRuleValidationError("invalid_service_days")


def _validate_date_exceptions(date_exceptions: object) -> None:
    if date_exceptions is None:
        return
    if not isinstance(date_exceptions, (list, tuple)):
        raise ServiceRuleValidationError("invalid_date_exceptions")
    for value in date_exceptions:
        if not isinstance(value, str):
            raise ServiceRuleValidationError("invalid_date_exceptions")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ServiceRuleValidationError("invalid_date_exceptions") from exc


def validate_service_rule(
    *,
    duration_minutes: int | None,
    working_hours: object,
    service_days: object,
    date_exceptions: object,
) -> None:
    """Raise :class:`ServiceRuleValidationError` if any field is malformed."""
    _validate_duration(duration_minutes)
    _validate_working_hours(working_hours)
    _validate_service_days(service_days)
    _validate_date_exceptions(date_exceptions)
