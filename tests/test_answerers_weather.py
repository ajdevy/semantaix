from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.weather_answerer import WeatherAnswerer, _resolve_query
from services.api.app.answerers.weather_client import WeatherSummary


def _ctx(location: str = "Moscow") -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
        now=datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        location=location,
    )


def _summary(name: str = "Moscow") -> WeatherSummary:
    return WeatherSummary(
        location_name=name,
        temperature_c=15.0,
        condition_ru="переменная облачность",
        condition_en="partly cloudy",
    )


@pytest.mark.asyncio
async def test_ru_weather_cyrillic_city_mapped_to_latin():
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary("Moscow"))})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="Какая погода в Москве?", ctx=_ctx())
    assert result.handled is True
    assert result.response_mode == "deterministic_weather"
    assert "Moscow" in result.text
    assert "15°C" in result.text
    assert "переменная облачность" in result.text
    client.fetch.assert_awaited_once_with(query="Moscow")


@pytest.mark.asyncio
async def test_ru_weather_no_city_uses_default_location():
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary("Moscow"))})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="Какая погода?", ctx=_ctx())
    assert result.handled is True
    client.fetch.assert_awaited_once_with(query="Moscow")


@pytest.mark.asyncio
async def test_en_weather_in_english():
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary("Berlin"))})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="weather in Berlin?", ctx=_ctx())
    assert result.handled is True
    assert "Now in Berlin" in result.text
    assert "partly cloudy" in result.text
    client.fetch.assert_awaited_once_with(query="berlin")


@pytest.mark.asyncio
async def test_cyrillic_city_not_in_map_falls_through():
    client = type("C", (), {"fetch": AsyncMock(return_value=None)})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(
        question="Какая погода в Урюпинске?", ctx=_ctx()
    )
    assert result.handled is False
    client.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_client_exception_falls_through():
    client = type("C", (), {"fetch": AsyncMock(side_effect=RuntimeError("network"))})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="Какая погода?", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_weather_client_empty_result_falls_through():
    client = type("C", (), {"fetch": AsyncMock(return_value=None)})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="weather in Atlantis?", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_no_weather_intent_returns_unhandled():
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary())})()
    answerer = WeatherAnswerer(client=client)
    result = await answerer.try_answer(question="Какое сегодня число?", ctx=_ctx())
    assert result.handled is False
    client.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_loc_capture_uses_default_location():
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary("Moscow"))})()
    answerer = WeatherAnswerer(client=client)
    # "weather in   ." -> captured loc is "  ." -> after strip + rstrip("." ) becomes "".
    # Should fall back to the default location.
    result = await answerer.try_answer(question="weather in .", ctx=_ctx())
    assert result.handled is True
    client.fetch.assert_awaited_once_with(query="Moscow")


def test_resolve_query_blank_captured_after_strip_falls_back_to_default():
    # Captured loc that becomes empty after strip+rstrip("." etc.)+lower
    # falls back to the default. This branch is unreachable through the
    # answerer regex (which requires a letter start), but the helper is
    # called directly when callers route through other paths.
    assert _resolve_query(".,!?", "Berlin") == "Berlin"


def test_resolve_query_cyrillic_no_lemmas_returns_none():
    # Construct a Cyrillic-only "loc" whose razdel + pymorphy3 produces no
    # alphanumeric lemmas (defensive path). The CYRILLIC THOUSANDS SIGN
    # is in the Cyrillic block but is treated as punctuation by razdel.
    cyrillic_punct = "҂"
    assert _resolve_query(cyrillic_punct, "Berlin") is None


@pytest.mark.asyncio
async def test_cyrillic_loc_with_no_lemmas_returns_none():
    # A captured Cyrillic loc string that lemmatizes to nothing
    # (pure punctuation in Cyrillic context) should fall through.
    client = type("C", (), {"fetch": AsyncMock(return_value=_summary("Moscow"))})()
    answerer = WeatherAnswerer(client=client)
    # The captured loc here would be all "—" which strips to empty lemmas.
    result = await answerer.try_answer(question="погода в ё", ctx=_ctx())
    # "ё" is not in the city map, but it does produce a lemma — should miss the map.
    assert result.handled is False
    client.fetch.assert_not_awaited()
