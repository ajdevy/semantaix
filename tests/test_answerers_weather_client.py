from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from services.api.app.answerers.weather_client import WeatherClient


def _http_responses(monkeypatch, *, geo_json: dict, forecast_json: dict):
    geo_resp = Mock()
    geo_resp.json.return_value = geo_json
    geo_resp.raise_for_status = Mock()
    forecast_resp = Mock()
    forecast_resp.json.return_value = forecast_json
    forecast_resp.raise_for_status = Mock()

    http_client = AsyncMock()
    http_client.get = AsyncMock(side_effect=[geo_resp, forecast_resp])

    cm = AsyncMock()
    cm.__aenter__.return_value = http_client
    cm.__aexit__.return_value = None
    monkeypatch.setattr(
        "services.api.app.answerers.weather_client.httpx.AsyncClient",
        lambda timeout: cm,
    )
    return http_client


@pytest.mark.asyncio
async def test_fetch_returns_summary_on_success(monkeypatch):
    _http_responses(
        monkeypatch,
        geo_json={
            "results": [
                {"name": "Moscow", "latitude": 55.75, "longitude": 37.61},
            ]
        },
        forecast_json={"current": {"temperature_2m": 15.0, "weather_code": 2}},
    )
    client = WeatherClient(base_url="https://api.open-meteo.com")
    summary = await client.fetch(query="Moscow")
    assert summary is not None
    assert summary.location_name == "Moscow"
    assert summary.temperature_c == 15.0
    assert summary.condition_ru == "переменная облачность"
    assert summary.condition_en == "partly cloudy"


@pytest.mark.asyncio
async def test_fetch_returns_none_when_geocoding_empty(monkeypatch):
    _http_responses(
        monkeypatch,
        geo_json={"results": []},
        forecast_json={},
    )
    client = WeatherClient(base_url="https://api.open-meteo.com")
    assert await client.fetch(query="Atlantis") is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_geocoding_missing_coords(monkeypatch):
    _http_responses(
        monkeypatch,
        geo_json={"results": [{"name": "X"}]},
        forecast_json={},
    )
    client = WeatherClient(base_url="https://api.open-meteo.com")
    assert await client.fetch(query="X") is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_forecast_missing_fields(monkeypatch):
    _http_responses(
        monkeypatch,
        geo_json={
            "results": [{"name": "Moscow", "latitude": 55.0, "longitude": 37.0}],
        },
        forecast_json={"current": {}},
    )
    client = WeatherClient(base_url="https://api.open-meteo.com")
    assert await client.fetch(query="Moscow") is None


@pytest.mark.asyncio
async def test_fetch_uses_unknown_weather_code_fallback(monkeypatch):
    _http_responses(
        monkeypatch,
        geo_json={
            "results": [{"name": "Moscow", "latitude": 55.0, "longitude": 37.0}],
        },
        forecast_json={"current": {"temperature_2m": 10.0, "weather_code": 9999}},
    )
    client = WeatherClient(base_url="https://api.open-meteo.com")
    summary = await client.fetch(query="Moscow")
    assert summary is not None
    assert summary.condition_ru == "погодные условия"
    assert summary.condition_en == "weather"
