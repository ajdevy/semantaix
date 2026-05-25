"""Regex parser tests for ``parse_service_intent`` (Epic 12, story 12.04).

Covers the FR-24 Path B "must parse" inventory (full field-set, alt verbs,
ё/е + Cyrillic-dash normalization, edit, remove) and the explicit
"must fail closed" inventory (multi-service "и", non-digit duration,
non-anchored utterance).
"""

from __future__ import annotations

import pytest

from services.api.app.services_nl_ops import (
    OP_SERVICE_ADD,
    OP_SERVICE_EDIT,
    OP_SERVICE_REMOVE,
    OP_UNKNOWN,
    REASON_MULTIPLE_SERVICES,
    REASON_NON_DIGIT_DURATION,
    REASON_UNRECOGNIZED,
    parse_service_intent,
)

# --- MUST PARSE -------------------------------------------------------------


def test_add_full_field_set():
    text = (
        "добавь услугу маникюр на 60 минут пн-сб 10-19 цена 2000 "
        "описание: классический и аппаратный"
    )
    result = parse_service_intent(text)
    assert result.op_type == OP_SERVICE_ADD
    p = result.payload
    assert p["name"] == "маникюр"
    assert p["duration_minutes"] == 60
    assert p["service_days"] == ["mon", "tue", "wed", "thu", "fri", "sat"]
    assert p["working_hours"]["mon"] == [["10:00", "19:00"]]
    assert p["price_text"] == "2000"
    assert p["description"] == "классический и аппаратный"


def test_add_alternate_verb_novaya_with_длительность():
    text = "новая услуга стрижка детская длительность 30 мин цена 1500"
    result = parse_service_intent(text)
    assert result.op_type == OP_SERVICE_ADD
    assert result.payload["name"] == "стрижка детская"
    assert result.payload["duration_minutes"] == 30
    assert result.payload["price_text"] == "1500"


@pytest.mark.parametrize("dash", ["-", "–", "—"])
def test_three_dash_variants_normalize_identically(dash):
    text = (
        f"добавь услугу маникюр на 60 минут пн{dash}сб 10{dash}19 цена 2000"
    )
    result = parse_service_intent(text)
    assert result.op_type == OP_SERVICE_ADD
    assert result.payload["service_days"] == [
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
    ]
    assert result.payload["working_hours"]["mon"] == [["10:00", "19:00"]]


def test_yo_and_e_normalize_identically():
    yo = parse_service_intent("создай услугу ёжик")
    ye = parse_service_intent("создай услугу ежик")
    assert yo.op_type == OP_SERVICE_ADD
    assert ye.op_type == OP_SERVICE_ADD
    # Internal normalization collapses both to the same canonical name.
    assert yo.payload["name"] == ye.payload["name"]


def test_edit_returns_service_edit_op():
    result = parse_service_intent("измени услугу маникюр цена 2500")
    assert result.op_type == OP_SERVICE_EDIT
    assert result.payload["name"] == "маникюр"
    assert result.payload["price_text"] == "2500"


def test_remove_returns_service_remove_op():
    result = parse_service_intent("удали услугу маникюр")
    assert result.op_type == OP_SERVICE_REMOVE
    assert result.payload == {"name": "маникюр"}


def test_nl_parser_accepts_add_name_only():
    """R1 refinement: a bare `добавь услугу <name>` is a catalog-only add."""
    result = parse_service_intent("добавь услугу маникюр")
    assert result.op_type == OP_SERVICE_ADD
    assert result.payload == {"name": "маникюр"}
    assert result.preview == "Создать услугу «маникюр»."


def test_nl_parser_accepts_hyphenated_name():
    """R1 refinement: hyphenated names must not be confused with the multi-
    service `<name> и <name>` pattern (which requires whitespace around 'и')."""
    result = parse_service_intent("добавь услугу маникюр-классика")
    assert result.op_type == OP_SERVICE_ADD
    assert result.payload["name"] == "маникюр-классика"


# --- MUST FAIL CLOSED -------------------------------------------------------


def test_multi_service_with_conjunction_fails_closed():
    result = parse_service_intent("добавь услугу маникюр и педикюр")
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_MULTIPLE_SERVICES
    assert "одной услуге" in result.preview


def test_non_digit_duration_polutora_fails_closed():
    result = parse_service_intent("добавь услугу маникюр на полтора часа")
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_NON_DIGIT_DURATION
    assert "числом" in result.preview


def test_non_digit_duration_chas_fails_closed():
    """'на час' (single hour) likewise lacks a digit-minute clause."""
    result = parse_service_intent("добавь услугу маникюр на час")
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_NON_DIGIT_DURATION


def test_not_anchored_to_start_of_message_fails_closed():
    result = parse_service_intent("мысль: добавь услугу маникюр")
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_UNRECOGNIZED


def test_non_string_input_fails_closed():
    result = parse_service_intent(None)  # type: ignore[arg-type]
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_UNRECOGNIZED


def test_empty_name_after_verb_fails_closed():
    """Trigger keyword present but no name → fails closed."""
    result = parse_service_intent("добавь услугу  ")
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_UNRECOGNIZED


# --- edge cases for branch coverage ----------------------------------------


def test_non_digit_minutes_clause_fails_closed():
    """Path: presence regex matches, capture group is non-digit (line 330)."""
    result = parse_service_intent(
        "добавь услугу маникюр длительность пять минут"
    )
    assert result.op_type == OP_UNKNOWN
    assert result.reason == REASON_NON_DIGIT_DURATION


def test_days_only_without_hours_renders_preview(tmp_path=None):
    """Days range without hours hits the days-only preview branch."""
    result = parse_service_intent(
        "добавь услугу маникюр на 60 минут пн-сб"
    )
    assert result.op_type == OP_SERVICE_ADD
    assert result.payload.get("service_days") == [
        "mon", "tue", "wed", "thu", "fri", "sat"
    ]
    # No hours captured → days-only preview branch
    assert "пн-сб" in result.preview
    assert "10:00" not in result.preview


def test_long_name_is_clipped_to_cap():
    """Operator text >200 chars is clipped (line 198)."""
    long = "a" * 250
    result = parse_service_intent(f"добавь услугу {long}")
    assert result.op_type == OP_SERVICE_ADD
    assert len(str(result.payload["name"])) == 200


def test_reverse_days_range_drops_service_days():
    """Reverse-direction days range (e.g. сб-пн) yields no service_days
    (line 208 returns []) so payload omits the field."""
    result = parse_service_intent(
        "добавь услугу маникюр на 60 минут сб-пн 10-19"
    )
    assert result.op_type == OP_SERVICE_ADD
    assert "service_days" not in result.payload
