"""Unit coverage for :mod:`project_service_validation` (Epic 12, story 12.02).

The helper rejects every malformed payload with a stable ``reason`` code that
the api endpoint translates 1:1 into a 400 response.
"""

from __future__ import annotations

import pytest

from services.api.app.calendar.project_service_validation import (
    ProjectServiceValidationError,
    validate_project_service,
)


def _ok(**overrides):
    base = {
        "name": "маникюр",
        "duration_minutes": None,
        "working_hours": None,
        "service_days": None,
        "date_exceptions": None,
    }
    base.update(overrides)
    return base


def test_valid_minimal_payload_returns_stripped_name():
    assert validate_project_service(**_ok(name="  маникюр  ")) == "маникюр"


def test_valid_full_payload_returns_name():
    assert (
        validate_project_service(
            **_ok(
                name="стрижка",
                duration_minutes=30,
                working_hours={"mon": [["10:00", "19:00"]]},
                service_days=["mon", "tue"],
                date_exceptions=["2026-01-01"],
            )
        )
        == "стрижка"
    )


@pytest.mark.parametrize("bad_name", [None, 42, ""])
def test_invalid_service_name_type_or_empty(bad_name):
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(**_ok(name=bad_name))
    assert exc.value.reason == "invalid_service_name"


def test_whitespace_only_name_rejected():
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(**_ok(name="   "))
    assert exc.value.reason == "invalid_service_name"


def test_invalid_duration_propagated():
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(**_ok(duration_minutes=-5))
    assert exc.value.reason == "invalid_duration"


def test_invalid_working_hours_propagated():
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(
            **_ok(working_hours={"mon": [["19:00", "10:00"]]})
        )
    assert exc.value.reason == "invalid_working_hours"


def test_invalid_service_days_propagated():
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(**_ok(service_days=["funday"]))
    assert exc.value.reason == "invalid_service_days"


def test_invalid_date_exceptions_propagated():
    with pytest.raises(ProjectServiceValidationError) as exc:
        validate_project_service(**_ok(date_exceptions=["not-iso"]))
    assert exc.value.reason == "invalid_date_exceptions"
