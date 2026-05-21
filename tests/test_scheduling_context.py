from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers import scheduling_context as sc
from services.api.app.answerers.weather_client import WeatherSummary


def _ctx(
    *,
    language: str = "ru",
    country_code: str = "RU",
    timezone: str = "Europe/Moscow",
    location: str = "Moscow",
    now: datetime | None = None,
) -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
        now=now or datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        language=language,
        country_code=country_code,
        timezone=timezone,
        location=location,
    )


def _summary(name: str = "Moscow") -> WeatherSummary:
    return WeatherSummary(
        location_name=name,
        temperature_c=15.0,
        condition_ru="переменная облачность",
        condition_en="partly cloudy",
    )


@pytest.mark.parametrize(
    "text",
    [
        "можете доставить заказ завтра?",
        "хочу заказать товар",
        "как купить ваш продукт?",
        "можно записаться на услугу?",
        "забронировать столик",
        "какое у вас расписание?",
        "can you deliver my order tomorrow?",
        "I want to buy this",
        "book an appointment please",
    ],
)
def test_intent_detected_for_scheduling_phrases(text):
    normalized = sc.get_russian_normalizer().normalize(text)
    assert sc.has_scheduling_intent(normalized) is True


@pytest.mark.parametrize(
    "text",
    [
        "когда придёт мой возврат?",
        "какое сегодня число?",
        "какая погода в Москве?",
        "what is the date?",
    ],
)
def test_intent_not_detected_for_non_scheduling(text):
    normalized = sc.get_russian_normalizer().normalize(text)
    assert sc.has_scheduling_intent(normalized) is False


@pytest.mark.asyncio
async def test_no_intent_returns_none():
    result = await sc.build_scheduling_context(
        question="когда придёт мой возврат?", ctx=_ctx(), weather_client=None
    )
    assert result is None


@pytest.mark.asyncio
async def test_russian_block_includes_datetime_and_holiday():
    # May 9 is Victory Day in RU; assert the holiday line + next holiday line.
    ctx = _ctx(now=datetime(2026, 5, 9, 6, 0, tzinfo=UTC))
    result = await sc.build_scheduling_context(
        question="можете доставить заказ?", ctx=ctx, weather_client=None
    )
    assert result is not None
    assert "Справочный контекст для планирования" in result
    assert "Текущие дата и время:" in result
    assert "9 мая 2026" in result
    assert "Сегодня праздник:" in result
    assert "Следующий праздник:" in result
    # No weather client -> no weather line.
    assert "Погода сейчас" not in result


@pytest.mark.asyncio
async def test_non_holiday_day_reports_not_a_holiday():
    ctx = _ctx(now=datetime(2026, 5, 11, 6, 0, tzinfo=UTC))
    result = await sc.build_scheduling_context(
        question="хочу заказать", ctx=ctx, weather_client=None
    )
    assert result is not None
    assert "Сегодня не праздник." in result


@pytest.mark.asyncio
async def test_english_block_uses_english_labels():
    ctx = _ctx(language="en", country_code="US", timezone="UTC")
    result = await sc.build_scheduling_context(
        question="can you deliver my order?", ctx=ctx, weather_client=None
    )
    assert result is not None
    assert "Reference context for scheduling" in result
    assert "Current date and time:" in result
    assert "Today is" in result


@pytest.mark.asyncio
async def test_weather_included_when_city_resolves():
    client = AsyncMock()
    client.fetch = AsyncMock(return_value=_summary("Moscow"))
    result = await sc.build_scheduling_context(
        question="можете доставить заказ в Москве?", ctx=_ctx(), weather_client=client
    )
    assert result is not None
    assert "Погода сейчас (Moscow): 15°C, переменная облачность." in result
    client.fetch.assert_awaited_once_with(query="Moscow")


@pytest.mark.asyncio
async def test_weather_falls_back_to_default_location_for_unknown_city():
    client = AsyncMock()
    client.fetch = AsyncMock(return_value=_summary("Moscow"))
    result = await sc.build_scheduling_context(
        question="можете доставить заказ в Урюпинске?", ctx=_ctx(), weather_client=client
    )
    assert result is not None
    client.fetch.assert_awaited_once_with(query="Moscow")


@pytest.mark.asyncio
async def test_weather_omitted_on_client_error():
    client = AsyncMock()
    client.fetch = AsyncMock(side_effect=RuntimeError("network"))
    result = await sc.build_scheduling_context(
        question="хочу заказать", ctx=_ctx(), weather_client=client
    )
    assert result is not None
    assert "Погода сейчас" not in result


@pytest.mark.asyncio
async def test_weather_omitted_when_summary_none():
    client = AsyncMock()
    client.fetch = AsyncMock(return_value=None)
    result = await sc.build_scheduling_context(
        question="хочу заказать", ctx=_ctx(), weather_client=client
    )
    assert result is not None
    assert "Погода сейчас" not in result


@pytest.mark.asyncio
async def test_english_location_capture_passed_to_client():
    client = AsyncMock()
    client.fetch = AsyncMock(return_value=_summary("Berlin"))
    ctx = _ctx(language="en", country_code="US", timezone="UTC")
    result = await sc.build_scheduling_context(
        question="can you deliver my order in Berlin?", ctx=ctx, weather_client=client
    )
    assert result is not None
    assert "Weather now (Berlin): 15°C, partly cloudy." in result
    client.fetch.assert_awaited_once_with(query="berlin")


@pytest.mark.asyncio
async def test_unknown_country_omits_holiday_lines():
    ctx = _ctx(country_code="ZZ")
    result = await sc.build_scheduling_context(
        question="хочу заказать", ctx=ctx, weather_client=None
    )
    assert result is not None
    # Unknown country -> holiday calendar None -> not-a-holiday + no next line.
    assert "Сегодня не праздник." in result
    assert "Следующий праздник:" not in result


@pytest.mark.asyncio
async def test_english_holiday_line_when_today_is_holiday():
    # US Independence Day, English labels.
    ctx = _ctx(
        language="en",
        country_code="US",
        timezone="UTC",
        now=datetime(2026, 7, 4, 12, 0, tzinfo=UTC),
    )
    result = await sc.build_scheduling_context(
        question="can you deliver my order?", ctx=ctx, weather_client=None
    )
    assert result is not None
    assert "Today is a holiday:" in result


@pytest.mark.asyncio
async def test_empty_calendar_yields_no_next_holiday(monkeypatch):
    # Calendar resolves but is empty in both years -> no next-holiday line and
    # _find_next_holiday exhausts its loop.
    monkeypatch.setattr(sc, "_resolve_country_holidays", lambda *a, **kw: {})
    result = await sc.build_scheduling_context(
        question="хочу заказать", ctx=_ctx(), weather_client=None
    )
    assert result is not None
    assert "Сегодня не праздник." in result
    assert "Следующий праздник:" not in result


def test_resolve_weather_query_blank_capture_uses_default():
    assert sc._resolve_weather_query("   ", "Moscow") == "Moscow"


def test_resolve_weather_query_cyrillic_unmapped_uses_default():
    assert sc._resolve_weather_query("Урюпинск", "Moscow") == "Moscow"
