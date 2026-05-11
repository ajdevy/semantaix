from __future__ import annotations

import re

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.answerers.weather_client import WeatherClient
from services.api.app.russian_text import get_russian_normalizer

# Top Russian cities (and nearby Russian-speaking population centers) that
# Open-Meteo geocoding accepts in their Latin form. This map exists because
# Open-Meteo's geocoding endpoint does not accept Cyrillic input. Add new
# entries as real customer traffic surfaces missed cities.
_RU_CITY_LATIN: dict[str, str] = {
    "москва": "Moscow",
    "санкт-петербург": "Saint Petersburg",
    "питер": "Saint Petersburg",
    "спб": "Saint Petersburg",
    "новосибирск": "Novosibirsk",
    "екатеринбург": "Yekaterinburg",
    "казань": "Kazan",
    "нижний новгород": "Nizhny Novgorod",
    "челябинск": "Chelyabinsk",
    "самара": "Samara",
    "омск": "Omsk",
    "ростов-на-дону": "Rostov-on-Don",
    "уфа": "Ufa",
    "красноярск": "Krasnoyarsk",
    "пермь": "Perm",
    "воронеж": "Voronezh",
    "волгоград": "Volgograd",
    "краснодар": "Krasnodar",
    "сочи": "Sochi",
    "тюмень": "Tyumen",
    "владивосток": "Vladivostok",
    "иркутск": "Irkutsk",
    "хабаровск": "Khabarovsk",
}

_RU_PATTERN = re.compile(
    r"(?:погода|температура|прогноз(?:\s+погоды)?)"
    r"(?:\s+(?:в|во)\s+(?P<loc>[а-яёА-ЯЁ][а-яёА-ЯЁ\-\s]*))?",
    re.IGNORECASE | re.UNICODE,
)

_EN_PATTERN = re.compile(
    r"(?:weather|temperature|forecast)"
    r"(?:\s+in\s+(?P<loc>[A-Za-z][A-Za-z\-\s]*))?",
    re.IGNORECASE,
)


def _resolve_query(captured: str | None, default_location: str) -> str | None:
    if not captured:
        return default_location
    raw = captured.strip().rstrip(".,!?").lower()
    if not raw:
        return default_location
    if any(ord(ch) >= 1024 for ch in raw):  # Cyrillic present
        # Lemmatize the captured city name to handle Russian inflection
        # ("Москве" -> "москва"), then look up in the Latin map.
        lemmas = get_russian_normalizer().lemmas(raw)
        if not lemmas:
            return None
        key = " ".join(lemmas)
        return _RU_CITY_LATIN.get(key)
    return raw


class WeatherAnswerer:
    name = "weather"

    def __init__(self, *, client: WeatherClient) -> None:
        self._client = client

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        normalized = get_russian_normalizer().normalize(question)
        ru_match = _RU_PATTERN.search(normalized)
        en_match = None if ru_match else _EN_PATTERN.search(normalized)
        if not (ru_match or en_match):
            return AnswerResult(handled=False)

        in_russian = bool(ru_match)
        match = ru_match or en_match
        assert match is not None
        captured = match.group("loc") if match.groupdict().get("loc") else None

        query = _resolve_query(captured, ctx.location)
        if query is None:
            return AnswerResult(handled=False)

        try:
            summary = await self._client.fetch(query=query)
        except Exception:
            return AnswerResult(handled=False)
        if summary is None:
            return AnswerResult(handled=False)

        if in_russian:
            text = (
                f"Сейчас в городе {summary.location_name}: "
                f"{summary.temperature_c:.0f}°C, {summary.condition_ru}."
            )
        else:
            text = (
                f"Now in {summary.location_name}: "
                f"{summary.temperature_c:.0f}°C, {summary.condition_en}."
            )
        return AnswerResult(
            handled=True,
            text=text,
            response_mode="deterministic_weather",
            metadata={"location": summary.location_name},
        )
