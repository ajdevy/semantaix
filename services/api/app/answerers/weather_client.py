from __future__ import annotations

from dataclasses import dataclass

import httpx

# https://open-meteo.com/en/docs#weathervariables — small subset.
_WMO_CODE_RU = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "облачно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "морось",
    55: "сильная морось",
    61: "лёгкий дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "лёгкий снег",
    73: "снег",
    75: "сильный снег",
    80: "ливень",
    81: "сильный ливень",
    82: "очень сильный ливень",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}

_WMO_CODE_EN = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "showers",
    81: "heavy showers",
    82: "violent showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "violent thunderstorm with hail",
}


@dataclass(frozen=True)
class WeatherSummary:
    location_name: str
    temperature_c: float
    condition_ru: str
    condition_en: str


class WeatherClient:
    def __init__(self, *, base_url: str, timeout_seconds: int = 10) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def fetch(self, *, query: str) -> WeatherSummary | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": 1, "language": "en"},
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
            if not results:
                return None
            top = results[0]
            latitude = top.get("latitude")
            longitude = top.get("longitude")
            location_name = top.get("name") or query
            if latitude is None or longitude is None:
                return None

            forecast = await client.get(
                f"{self._base_url}/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code",
                },
            )
            forecast.raise_for_status()
            current = (forecast.json() or {}).get("current") or {}
            temperature = current.get("temperature_2m")
            weather_code = current.get("weather_code")
            if temperature is None or weather_code is None:
                return None
            return WeatherSummary(
                location_name=str(location_name),
                temperature_c=float(temperature),
                condition_ru=_WMO_CODE_RU.get(int(weather_code), "погодные условия"),
                condition_en=_WMO_CODE_EN.get(int(weather_code), "weather"),
            )
