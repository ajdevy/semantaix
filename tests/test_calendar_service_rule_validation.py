"""Unit coverage for the service-rule write validator (Epic 11, story 11.08)."""

from __future__ import annotations

import pytest

from services.api.app.calendar.service_rule_validation import (
    ServiceRuleValidationError,
    validate_service_rule,
)


def _validate(**overrides):
    payload = {
        "duration_minutes": None,
        "working_hours": None,
        "service_days": None,
        "date_exceptions": None,
    }
    payload.update(overrides)
    validate_service_rule(**payload)


def test_all_none_is_valid():
    _validate()


def test_full_valid_payload():
    _validate(
        duration_minutes=60,
        working_hours={"mon": ["09:00", "18:00"], "tue": [["09:00", "13:00"], ["14:00", "18:00"]]},
        service_days=["mon", "Tue"],
        date_exceptions=["2026-01-01"],
    )


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_duration_rejected(value):
    with pytest.raises(ServiceRuleValidationError) as exc:
        _validate(duration_minutes=value)
    assert exc.value.reason == "invalid_duration"


def test_bool_duration_rejected():
    with pytest.raises(ServiceRuleValidationError) as exc:
        _validate(duration_minutes=True)
    assert exc.value.reason == "invalid_duration"


def test_working_hours_not_dict_rejected():
    with pytest.raises(ServiceRuleValidationError) as exc:
        _validate(working_hours=["mon"])
    assert exc.value.reason == "invalid_working_hours"


def test_working_hours_unknown_day_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"funday": ["09:00", "18:00"]})


def test_working_hours_non_string_day_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={1: ["09:00", "18:00"]})


def test_working_hours_empty_value_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"mon": []})


def test_working_hours_bad_pair_length_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"mon": [["09:00"]]})


def test_working_hours_non_string_time_rejected():
    # A nested pair whose elements are not strings: hits the per-pair string
    # check rather than the pair-shape check.
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"mon": [[9, 18]]})


def test_working_hours_unparseable_time_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"mon": ["xx:yy", "18:00"]})


def test_working_hours_start_after_end_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(working_hours={"mon": ["19:00", "09:00"]})


def test_service_days_not_list_rejected():
    with pytest.raises(ServiceRuleValidationError) as exc:
        _validate(service_days="mon")
    assert exc.value.reason == "invalid_service_days"


def test_service_days_unknown_token_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(service_days=["mon", "noday"])


def test_date_exceptions_not_list_rejected():
    with pytest.raises(ServiceRuleValidationError) as exc:
        _validate(date_exceptions="2026-01-01")
    assert exc.value.reason == "invalid_date_exceptions"


def test_date_exceptions_non_string_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(date_exceptions=[20260101])


def test_date_exceptions_bad_iso_rejected():
    with pytest.raises(ServiceRuleValidationError):
        _validate(date_exceptions=["not-a-date"])
