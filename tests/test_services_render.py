"""Unit tests for ``services.api.app.services_render`` (story 12.06 / FR-25).

The hard structural guarantee FR-25 relies on is asserted here: no field
labels (``Название:`` / ``Описание:`` / ``Цена:`` / ``Длительность:`` /
``Дни:`` / ``Часы:``) appear in any output for any row shape.
"""

from __future__ import annotations

import json

import pytest

from services.api.app import services_render
from services.api.app.calendar.project_services_repository import ProjectService
from services.api.app.services_render import (
    _render_date_exceptions,
    _render_days,
    _render_duration,
    _render_hours,
    _render_price,
    get_terms_data,
    load_russian_calendar_terms,
    render_project_service_prose,
    render_project_services_block,
    reset_terms_cache_for_tests,
)

_FORBIDDEN_LABELS = (
    "Название:",
    "Описание:",
    "Цена:",
    "Длительность:",
    "Дни:",
    "Часы:",
)


def _make_service(
    *,
    name: str = "Маникюр",
    description: str | None = None,
    price_text: str | None = None,
    tags: list | None = None,
    duration_minutes: int | None = None,
    working_hours: dict | None = None,
    service_days: list | None = None,
    date_exceptions: list | None = None,
) -> ProjectService:
    return ProjectService(
        id=1,
        project_id=1,
        name=name,
        description=description,
        price_text=price_text,
        tags=tags,
        duration_minutes=duration_minutes,
        working_hours=working_hours,
        service_days=service_days,
        date_exceptions=date_exceptions,
        updated_at=None,
    )


@pytest.fixture
def terms() -> dict:
    return load_russian_calendar_terms()


def _assert_no_labels(text: str) -> None:
    for label in _FORBIDDEN_LABELS:
        assert label not in text, f"label {label!r} leaked into output: {text!r}"


def test_load_russian_calendar_terms_returns_expected_keys():
    data = load_russian_calendar_terms()
    assert "weekday_short" in data
    assert "month_genitive" in data
    assert data["weekday_short"]["mon"] == "пн"
    assert data["month_genitive"]["1"] == "января"


def test_get_terms_data_is_cached(tmp_path):
    reset_terms_cache_for_tests()
    path = tmp_path / "terms.json"
    payload = {"weekday_short": {"mon": "пн"}, "month_genitive": {"1": "января"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    first = get_terms_data(path=str(path))
    second = get_terms_data(path=str(path))
    assert first is second
    # And the default-path call returns the real terms data (separate cache key).
    assert get_terms_data()["closed_prefix"] == "закрыто:"


def test_render_duration_plural_forms():
    assert _render_duration(1) == "1 минута"
    assert _render_duration(2) == "2 минуты"
    assert _render_duration(4) == "4 минуты"
    assert _render_duration(5) == "5 минут"
    assert _render_duration(11) == "11 минут"
    assert _render_duration(14) == "14 минут"
    assert _render_duration(21) == "21 минута"
    assert _render_duration(60) == "60 минут"
    assert _render_duration(None) is None
    assert _render_duration(0) is None


def test_render_days_full_week_partial_and_range(terms):
    assert (
        _render_days(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], terms=terms)
        == "ежедневно"
    )
    assert (
        _render_days(["mon", "tue", "wed", "thu", "fri", "sat"], terms=terms)
        == "пн-сб"
    )
    assert (
        _render_days(["mon", "wed", "fri"], terms=terms)
        == "пн, ср, пт"
    )
    assert _render_days(None, terms=terms) is None
    assert _render_days([], terms=terms) is None
    # Unknown codes get dropped — empty result returns None.
    assert _render_days(["foo"], terms=terms) is None


def test_render_hours_single_and_multi_window(terms):
    single = _render_hours({"mon": [["10:00", "19:00"]]}, terms=terms)
    assert single == "10:00-19:00"
    multi = _render_hours(
        {"mon": [["10:00", "13:00"], ["14:00", "19:00"]]}, terms=terms
    )
    assert multi == "10:00-13:00, 14:00-19:00"
    assert _render_hours(None, terms=terms) is None
    assert _render_hours({}, terms=terms) is None
    # Malformed window list collapses to None.
    assert _render_hours({"mon": [[]]}, terms=terms) is None
    # Picks first day in weekday order when multiple are present.
    mixed = _render_hours(
        {"fri": [["09:00", "12:00"]], "mon": [["10:00", "19:00"]]},
        terms=terms,
    )
    assert mixed == "10:00-19:00"
    # No known day present → None.
    assert _render_hours({"foo": [["1", "2"]]}, terms=terms) is None


def test_render_date_exceptions_single_and_multiple(terms):
    assert (
        _render_date_exceptions(["2026-01-01"], terms=terms)
        == "закрыто: 1 января"
    )
    assert (
        _render_date_exceptions(["2026-01-01", "2026-05-09"], terms=terms)
        == "закрыто: 1 января, 9 мая"
    )
    assert _render_date_exceptions(None, terms=terms) is None
    assert _render_date_exceptions([], terms=terms) is None
    # Malformed entry skipped silently.
    assert _render_date_exceptions(["not-a-date"], terms=terms) is None
    # Bad month index skipped.
    assert _render_date_exceptions(["2026-13-01"], terms=terms) is None


def test_render_price_passthrough_and_strip():
    assert _render_price("от 2000 ₽") == "цена от 2000 ₽"
    assert _render_price("  от 1000  ") == "цена от 1000"
    assert _render_price(None) is None
    assert _render_price("") is None
    assert _render_price("   ") is None


def test_full_row_renders_natural_prose_no_labels(terms):
    service = _make_service(
        name="Маникюр",
        description="Классический и аппаратный",
        price_text="от 2000 ₽",
        duration_minutes=60,
        working_hours={
            "mon": [["10:00", "19:00"]],
            "tue": [["10:00", "19:00"]],
            "wed": [["10:00", "19:00"]],
            "thu": [["10:00", "19:00"]],
            "fri": [["10:00", "19:00"]],
            "sat": [["10:00", "19:00"]],
        },
        service_days=["mon", "tue", "wed", "thu", "fri", "sat"],
    )
    rendered = render_project_service_prose(service, terms=terms)
    _assert_no_labels(rendered)
    assert rendered.startswith("Маникюр — 60 минут, пн-сб 10:00-19:00, цена от 2000 ₽.")
    assert "Классический и аппаратный" in rendered


def test_catalog_only_row(terms):
    service = _make_service(
        name="Маникюр",
        description="Классический и аппаратный",
        price_text="от 2000 ₽",
    )
    rendered = render_project_service_prose(service, terms=terms)
    _assert_no_labels(rendered)
    assert rendered == "Маникюр — цена от 2000 ₽. Классический и аппаратный."


def test_name_only_row(terms):
    rendered = render_project_service_prose(_make_service(name="Маникюр"), terms=terms)
    _assert_no_labels(rendered)
    assert rendered == "Маникюр."


def test_name_plus_price_only(terms):
    rendered = render_project_service_prose(
        _make_service(name="Маникюр", price_text="от 2000 ₽"), terms=terms
    )
    _assert_no_labels(rendered)
    assert rendered == "Маникюр — цена от 2000 ₽."


def test_days_only_without_hours(terms):
    service = _make_service(
        name="Маникюр", service_days=["mon", "tue", "wed", "thu", "fri", "sat"]
    )
    rendered = render_project_service_prose(service, terms=terms)
    _assert_no_labels(rendered)
    assert rendered == "Маникюр — пн-сб."


def test_hours_only_without_days(terms):
    service = _make_service(
        name="Маникюр", working_hours={"mon": [["10:00", "19:00"]]}
    )
    rendered = render_project_service_prose(service, terms=terms)
    _assert_no_labels(rendered)
    assert rendered == "Маникюр — 10:00-19:00."


def test_date_exceptions_included(terms):
    service = _make_service(
        name="Маникюр",
        duration_minutes=60,
        date_exceptions=["2026-01-01", "2026-05-09"],
    )
    rendered = render_project_service_prose(service, terms=terms)
    _assert_no_labels(rendered)
    assert "закрыто: 1 января, 9 мая" in rendered


def test_description_already_terminated_keeps_single_period(terms):
    service = _make_service(name="Маникюр", description="Только женский.")
    rendered = render_project_service_prose(service, terms=terms)
    assert rendered == "Маникюр. Только женский."


def test_empty_name_returns_empty_string(terms):
    service = _make_service(name="")
    assert render_project_service_prose(service, terms=terms) == ""


def test_render_block_joins_with_bullets(terms):
    rows = [
        _make_service(name="Маникюр", price_text="от 2000 ₽"),
        _make_service(name="Педикюр", price_text="от 2500 ₽"),
    ]
    block = render_project_services_block(rows, terms=terms)
    _assert_no_labels(block)
    assert block.splitlines() == [
        "• Маникюр — цена от 2000 ₽.",
        "• Педикюр — цена от 2500 ₽.",
    ]


def test_render_block_skips_empty_rows(terms):
    rows = [
        _make_service(name=""),
        _make_service(name="Маникюр"),
    ]
    block = render_project_services_block(rows, terms=terms)
    assert block == "• Маникюр."


def test_reset_terms_cache_for_tests_clears(tmp_path):
    reset_terms_cache_for_tests()
    path = tmp_path / "terms.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    first = get_terms_data(path=str(path))
    path.write_text(json.dumps({"a": 2}), encoding="utf-8")
    # Without reset, cache returns first version.
    assert get_terms_data(path=str(path)) is first
    reset_terms_cache_for_tests()
    refreshed = get_terms_data(path=str(path))
    assert refreshed == {"a": 2}


def test_module_exposes_services_render_module():
    # Anchor the module import so coverage tracks the top-level statements.
    assert hasattr(services_render, "render_project_service_prose")
