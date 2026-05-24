"""Exhaustive unit matrix for the pure availability engine (story 11.05).

Deterministic: every test injects a frozen tz-aware ``now`` and a fixed
``project_tz``; no real clock is ever read. Covers every ``reason`` branch, the
available path, DST spring-forward, the shipped ``Europe/Moscow`` offset, and
the rule-parsing helpers.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.api.app.calendar.availability import (
    REASON_BUSY,
    REASON_DATE_EXCEPTION,
    REASON_IN_PAST,
    REASON_OUTSIDE_LOOKAHEAD,
    REASON_OUTSIDE_WORKING_HOURS,
    REASON_WRONG_SERVICE_DAY,
    AvailabilityServiceRule,
    WorkingWindow,
    compute_availability,
    parse_service_rule,
)
from services.api.app.calendar.calendar_client import BusyInterval
from services.api.app.calendar.settings_repository import ServiceRule

MOSCOW = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")

# A Monday in Moscow, well clear of any RU public holiday.
_MONDAY = date(2026, 6, 1)  # 2026-06-01 is a Monday


def _window(start: str, end: str) -> WorkingWindow:
    return WorkingWindow(start=time.fromisoformat(start), end=time.fromisoformat(end))


def _rule(
    *,
    duration_minutes: int = 60,
    lookahead_days: int = 60,
    working_hours: dict | None = None,
    service_days: frozenset | None = None,
    date_exceptions: frozenset | None = None,
    country_code: str = "RU",
) -> AvailabilityServiceRule:
    """A Monday-only 09:00–18:00 rule unless overridden."""
    return AvailabilityServiceRule(
        duration_minutes=duration_minutes,
        lookahead_days=lookahead_days,
        working_hours=working_hours
        if working_hours is not None
        else {0: (_window("09:00", "18:00"),)},
        service_days=service_days if service_days is not None else frozenset({0}),
        date_exceptions=date_exceptions if date_exceptions is not None else frozenset(),
        country_code=country_code,
    )


def _msk(local_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=MOSCOW)


# --- compute_availability: the available path ---------------------------------


def test_free_within_hours_service_day_is_available():
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 10, 0)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=(),
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.available is True
    assert result.reason is None


# --- busy ---------------------------------------------------------------------


def test_full_overlap_busy():
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 10, 0)
    busy = (BusyInterval(start=_msk(_MONDAY, 9, 30), end=_msk(_MONDAY, 11, 0)),)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=busy,
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.available is False
    assert result.reason == REASON_BUSY


def test_partial_overlap_at_block_start_edge_is_busy():
    # Busy ends exactly at requested start would NOT overlap (half-open); a busy
    # that ends one minute INTO the block does overlap.
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 10, 0)
    busy = (BusyInterval(start=_msk(_MONDAY, 9, 0), end=_msk(_MONDAY, 10, 1)),)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=busy,
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_BUSY


def test_busy_abutting_block_end_does_not_overlap():
    # Busy starts exactly at block end (11:00) — half-open, no overlap → free.
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 10, 0)
    busy = (BusyInterval(start=_msk(_MONDAY, 11, 0), end=_msk(_MONDAY, 12, 0)),)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=busy,
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.available is True


# --- outside_working_hours ----------------------------------------------------


def test_block_runs_past_window_end_is_outside_working_hours():
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 17, 30)  # +60min → 18:30 > 18:00 window end
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=(),
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_OUTSIDE_WORKING_HOURS


def test_lunch_gap_slot_inside_gap_not_available_before_and_after_available():
    lunch = {0: (_window("09:00", "13:00"), _window("14:00", "18:00"))}
    rule = _rule(working_hours=lunch)
    now = _msk(_MONDAY, 8, 0)

    # Inside the gap (13:00–14:00) → outside working hours.
    inside = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 13, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert inside.reason == REASON_OUTSIDE_WORKING_HOURS

    # Before the gap.
    before = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 11, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert before.available is True

    # After the gap.
    after = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 15, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert after.available is True


def test_no_window_for_weekday_is_outside_working_hours():
    # Service-day configured but no working_hours entry for it.
    rule = _rule(working_hours={})
    now = _msk(_MONDAY, 8, 0)
    result = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 10, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_OUTSIDE_WORKING_HOURS


# --- wrong_service_day --------------------------------------------------------


def test_non_service_weekday_is_wrong_service_day():
    tuesday = date(2026, 6, 2)
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(tuesday, 10, 0)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=(),
        service_rule=_rule(),  # Monday-only
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_WRONG_SERVICE_DAY


# --- date_exception -----------------------------------------------------------


def test_explicit_date_exception():
    rule = _rule(date_exceptions=frozenset({_MONDAY}))
    now = _msk(_MONDAY, 8, 0)
    result = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 10, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_DATE_EXCEPTION


def test_ru_public_holiday_is_date_exception():
    # 2026-01-01 is New Year — an RU public holiday (also a Thursday).
    holiday = date(2026, 1, 1)
    rule = _rule(service_days=frozenset({holiday.weekday()}))
    now = datetime(2025, 12, 1, 8, 0, tzinfo=MOSCOW)
    result = compute_availability(
        now=now,
        requested_start=_msk(holiday, 10, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_DATE_EXCEPTION


def test_unknown_country_code_does_not_treat_day_as_holiday():
    # An unrecognised country → holiday lookup is a no-op, so a normal service
    # day stays available.
    rule = _rule(country_code="ZZ")
    now = _msk(_MONDAY, 8, 0)
    result = compute_availability(
        now=now,
        requested_start=_msk(_MONDAY, 10, 0),
        busy=(),
        service_rule=rule,
        project_tz=MOSCOW,
    )
    assert result.available is True


# --- in_past ------------------------------------------------------------------


def test_past_request_is_in_past():
    now = _msk(_MONDAY, 12, 0)
    requested = _msk(_MONDAY, 10, 0)  # before now
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=(),
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_IN_PAST


# --- outside_lookahead --------------------------------------------------------


def test_beyond_lookahead_is_outside_lookahead():
    now = _msk(_MONDAY, 8, 0)
    far = _msk(_MONDAY + timedelta(days=70), 10, 0)  # > 60-day horizon
    result = compute_availability(
        now=now,
        requested_start=far,
        busy=(),
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_OUTSIDE_LOOKAHEAD


# --- DST ----------------------------------------------------------------------


def test_dst_spring_forward_day_classified_correctly():
    # Europe/Berlin springs forward on 2026-03-29 (02:00 -> 03:00). A 10:00
    # local request that day is +02:00, not +01:00 — the engine must use the
    # post-transition offset. 2026-03-29 is a Sunday.
    spring = date(2026, 3, 29)
    rule = _rule(
        service_days=frozenset({spring.weekday()}),
        working_hours={spring.weekday(): (_window("09:00", "18:00"),)},
    )
    now = datetime(2026, 3, 1, 8, 0, tzinfo=BERLIN)
    requested = datetime(2026, 3, 29, 10, 0, tzinfo=BERLIN)
    # A busy block expressed in UTC at 08:00Z == 10:00 Berlin summer time.
    busy = (BusyInterval(start=datetime(2026, 3, 29, 8, 0, tzinfo=UTC),
                         end=datetime(2026, 3, 29, 8, 30, tzinfo=UTC)),)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=busy,
        service_rule=rule,
        project_tz=BERLIN,
    )
    # Post-transition, 10:00 local == 08:00Z, so the busy block overlaps.
    assert result.reason == REASON_BUSY


def test_moscow_offset_classified_correctly():
    # Moscow is a fixed +03:00 (no DST since 2014). 10:00 MSK == 07:00Z.
    now = _msk(_MONDAY, 8, 0)
    requested = _msk(_MONDAY, 10, 0)
    busy = (BusyInterval(start=datetime(2026, 6, 1, 7, 0, tzinfo=UTC),
                         end=datetime(2026, 6, 1, 7, 30, tzinfo=UTC)),)
    result = compute_availability(
        now=now,
        requested_start=requested,
        busy=busy,
        service_rule=_rule(),
        project_tz=MOSCOW,
    )
    assert result.reason == REASON_BUSY


# --- parse_service_rule -------------------------------------------------------


def test_parse_service_rule_flat_pair_and_list_of_pairs():
    row = ServiceRule(
        id=1,
        project_id=1,
        name="haircut",
        duration_minutes=45,
        working_hours={
            "mon": ["09:00", "18:00"],
            "tue": [["09:00", "13:00"], ["14:00", "18:00"]],
        },
        service_days=["mon", "tue"],
        date_exceptions=["2026-01-01"],
        updated_at=None,
    )
    parsed = parse_service_rule(row, lookahead_days=30, country_code="RU")
    assert parsed.duration_minutes == 45
    assert parsed.lookahead_days == 30
    assert parsed.working_hours[0] == (_window("09:00", "18:00"),)
    assert parsed.working_hours[1] == (_window("09:00", "13:00"), _window("14:00", "18:00"))
    assert parsed.service_days == frozenset({0, 1})
    assert parsed.date_exceptions == frozenset({date(2026, 1, 1)})
    assert parsed.country_code == "RU"


def test_parse_service_rule_none_fields_use_defaults():
    row = ServiceRule(
        id=1,
        project_id=1,
        name=None,
        duration_minutes=None,
        working_hours=None,
        service_days=None,
        date_exceptions=None,
        updated_at=None,
    )
    parsed = parse_service_rule(row)
    assert parsed.duration_minutes == 60  # fallback
    assert parsed.lookahead_days == 60  # default
    assert parsed.working_hours == {}
    assert parsed.service_days == frozenset()
    assert parsed.date_exceptions == frozenset()


def test_parse_service_rule_ignores_unknown_weekday_tokens_and_empty_windows():
    row = ServiceRule(
        id=1,
        project_id=1,
        name=None,
        duration_minutes=30,
        working_hours={"mon": ["09:00", "18:00"], "xxx": ["09:00", "10:00"], "wed": []},
        service_days=["mon", "zzz"],
        date_exceptions=[],
        updated_at=None,
    )
    parsed = parse_service_rule(row)
    # "xxx" is dropped; "wed" has empty windows so it is dropped too.
    assert set(parsed.working_hours.keys()) == {0}
    assert parsed.service_days == frozenset({0})
