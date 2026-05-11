from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.holiday_answerer import HolidayAnswerer


def _ctx(
    *,
    now: datetime,
    country_code: str = "RU",
    language: str = "ru",
    timezone: str = "Europe/Moscow",
) -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
        now=now,
        language=language,
        timezone=timezone,
        country_code=country_code,
    )


@pytest.mark.asyncio
async def test_ru_holiday_today_known_date():
    # 9 мая (Victory Day) — federal holiday in RU calendar.
    ctx = _ctx(now=datetime(2026, 5, 9, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Сегодня праздник?", ctx=ctx
    )
    assert result.handled is True
    assert result.response_mode == "deterministic_holiday"
    assert "Сегодня" in result.text


@pytest.mark.asyncio
async def test_ru_holiday_today_not_holiday():
    # 11 мая 2026 is a Monday after May holidays — not a federal holiday.
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Сегодня праздник?", ctx=ctx
    )
    assert result.handled is True
    assert "не праздник" in result.text


@pytest.mark.asyncio
async def test_ru_next_holiday_returns_upcoming_date():
    # From 2026-05-11, next federal holiday is 12 июня (День России).
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Какой следующий праздник?", ctx=ctx
    )
    assert result.handled is True
    assert "12.06.2026" in result.text


@pytest.mark.asyncio
async def test_en_holiday_match_returns_english_phrasing():
    ctx = _ctx(
        now=datetime(2026, 5, 9, 12, 0, tzinfo=UTC),
        country_code="US",
        language="en",
        timezone="UTC",
    )
    result = await HolidayAnswerer().try_answer(
        question="Next holiday please", ctx=ctx
    )
    assert result.handled is True
    assert "Next holiday in US" in result.text


@pytest.mark.asyncio
async def test_unknown_country_falls_through():
    ctx = _ctx(
        now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC), country_code="ZZ"
    )
    result = await HolidayAnswerer().try_answer(
        question="Сегодня праздник?", ctx=ctx
    )
    assert result.handled is False


@pytest.mark.asyncio
async def test_no_holiday_intent_returns_unhandled():
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Какая сегодня погода?", ctx=ctx
    )
    assert result.handled is False


@pytest.mark.asyncio
async def test_find_next_holiday_returns_none_when_no_upcoming_in_either_year(
    monkeypatch,
):
    # Force _resolve_country_holidays to return an empty calendar (no holidays
    # in current or next year) so the helper falls through to the final
    # `return None, None` branch and the answerer escalates.
    from services.api.app.answerers import holiday_answerer as ha

    monkeypatch.setattr(
        ha, "_resolve_country_holidays", lambda *a, **kw: {}
    )
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Какой следующий праздник?", ctx=ctx
    )
    assert result.handled is False


@pytest.mark.asyncio
async def test_find_next_holiday_handles_calendar_none_in_second_year(monkeypatch):
    # First year resolves OK but with no upcoming dates; second year fails to
    # resolve at all. Forces the line-118 early return inside the loop.
    from services.api.app.answerers import holiday_answerer as ha

    calls = {"count": 0}

    def _resolver(country_code, year, *, language):
        calls["count"] += 1
        if calls["count"] == 1:
            return {}  # empty current year
        return None  # year+1 resolves to None

    monkeypatch.setattr(ha, "_resolve_country_holidays", _resolver)
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await HolidayAnswerer().try_answer(
        question="Какой следующий праздник?", ctx=ctx
    )
    assert result.handled is False
