"""Optional scheduling-context enrichment for the grounded answerer.

Datetime, holiday, and weather used to be standalone pipeline stages that
answered factual questions directly. They now serve a different role: when a
customer message shows *scheduling intent* (arranging a delivery, booking a
service, buying an item), these signals are gathered into a context block that
is handed to the grounded LLM as supporting facts. The block is never a
customer-visible answer on its own — it only helps the grounded answerer reason
about scheduling questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import holidays as _holidays_lib

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.weather_client import WeatherSummary
from services.api.app.russian_text import get_russian_normalizer

_RU_MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

_EN_MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

# Scheduling/commerce *action* stems. Matched as substrings on normalized text
# (RU) or with word boundaries (EN). Bare temporal words ("когда", "tomorrow")
# deliberately do NOT trigger enrichment — only an intent to arrange/buy does.
_RU_INTENT = re.compile(
    r"достав|заказ|закаж|куп|запис|запиш|брон|расписани|оформит|назнач",
    re.IGNORECASE | re.UNICODE,
)
_EN_INTENT = re.compile(
    r"\b(?:deliver|order|buy|purchas|book|reserv|schedul|appointment)",
    re.IGNORECASE,
)

# Location capture for opportunistic weather lookup ("... в Москве", "... in Berlin").
_RU_LOC = re.compile(
    r"(?:в|во)\s+(?P<loc>[а-яёА-ЯЁ][а-яёА-ЯЁ\-\s]*)",
    re.IGNORECASE | re.UNICODE,
)
_EN_LOC = re.compile(
    r"\bin\s+(?P<loc>[A-Za-z][A-Za-z\-\s]*)",
    re.IGNORECASE,
)

# Top Russian-speaking population centers in their Latin form. Open-Meteo's
# geocoding endpoint does not accept Cyrillic input, so Cyrillic city mentions
# are lemmatized and mapped here. Add entries as real traffic surfaces misses.
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


class _WeatherFetcher(Protocol):
    async def fetch(self, *, query: str) -> WeatherSummary | None: ...


@dataclass(frozen=True)
class SchedulingSignals:
    now_local: str
    today_holiday: str | None
    next_holiday: tuple[date, str] | None
    weather: WeatherSummary | None


def has_scheduling_intent(normalized: str) -> bool:
    return bool(_RU_INTENT.search(normalized) or _EN_INTENT.search(normalized))


def format_local_datetime(now: datetime, timezone: str, *, in_russian: bool) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    if in_russian:
        month = _RU_MONTHS[local.month]
        return (
            f"Сейчас {local.strftime('%H:%M')} ({timezone}), "
            f"{local.day} {month} {local.year} г."
        )
    month = _EN_MONTHS[local.month]
    return (
        f"It is {local.strftime('%H:%M')} ({timezone}), "
        f"{month} {local.day}, {local.year}."
    )


def _resolve_country_holidays(country_code: str, year: int, *, language: str):
    try:
        return _holidays_lib.country_holidays(
            country_code, years=year, language=language
        )
    except (NotImplementedError, KeyError):
        return None


def _find_next_holiday(
    today: date, country_code: str, *, language: str
) -> tuple[date, str] | None:
    for year in (today.year, today.year + 1):
        calendar = _resolve_country_holidays(country_code, year, language=language)
        if calendar is None:
            return None
        upcoming = sorted(d for d in calendar.keys() if d > today)
        if upcoming:
            first = upcoming[0]
            return first, calendar[first]
    return None


def _resolve_weather_query(captured: str | None, default_location: str) -> str:
    if not captured:
        return default_location
    raw = captured.strip().rstrip(".,!?").lower()
    if not raw:
        return default_location
    if any(ord(ch) >= 1024 for ch in raw):  # Cyrillic present
        lemmas = get_russian_normalizer().lemmas(raw)
        key = " ".join(lemmas)
        return _RU_CITY_LATIN.get(key) or default_location
    return raw


async def _fetch_weather(
    normalized: str, ctx: AnswerContext, weather_client: _WeatherFetcher
) -> WeatherSummary | None:
    match = _RU_LOC.search(normalized) or _EN_LOC.search(normalized)
    captured = match.group("loc") if match else None
    query = _resolve_weather_query(captured, ctx.location)
    try:
        return await weather_client.fetch(query=query)
    except Exception:
        return None


def _collect_signals(
    normalized: str,
    ctx: AnswerContext,
    *,
    in_russian: bool,
    weather: WeatherSummary | None,
) -> SchedulingSignals:
    today_local = ctx.now.astimezone(ZoneInfo(ctx.timezone)).date()
    language = "ru" if in_russian else "en"
    calendar = _resolve_country_holidays(
        ctx.country_code, today_local.year, language=language
    )
    today_holiday = calendar.get(today_local) if calendar is not None else None
    next_holiday = _find_next_holiday(today_local, ctx.country_code, language=language)
    return SchedulingSignals(
        now_local=format_local_datetime(ctx.now, ctx.timezone, in_russian=in_russian),
        today_holiday=today_holiday,
        next_holiday=next_holiday,
        weather=weather,
    )


def _format_block(signals: SchedulingSignals, *, in_russian: bool) -> str:
    lines: list[str] = []
    if in_russian:
        lines.append(
            "Справочный контекст для планирования "
            "(актуальные факты, можно использовать в ответе):"
        )
        lines.append(f"- Текущие дата и время: {signals.now_local}")
        if signals.today_holiday:
            lines.append(f"- Сегодня праздник: {signals.today_holiday}.")
        else:
            lines.append("- Сегодня не праздник.")
        if signals.next_holiday is not None:
            next_date, next_name = signals.next_holiday
            lines.append(
                f"- Следующий праздник: {next_date.strftime('%d.%m.%Y')} — {next_name}."
            )
        if signals.weather is not None:
            lines.append(
                f"- Погода сейчас ({signals.weather.location_name}): "
                f"{signals.weather.temperature_c:.0f}°C, {signals.weather.condition_ru}."
            )
    else:
        lines.append(
            "Reference context for scheduling "
            "(current facts you may use in your answer):"
        )
        lines.append(f"- Current date and time: {signals.now_local}")
        if signals.today_holiday:
            lines.append(f"- Today is a holiday: {signals.today_holiday}.")
        else:
            lines.append("- Today is not a holiday.")
        if signals.next_holiday is not None:
            next_date, next_name = signals.next_holiday
            lines.append(
                f"- Next holiday: {next_date.strftime('%Y-%m-%d')} — {next_name}."
            )
        if signals.weather is not None:
            lines.append(
                f"- Weather now ({signals.weather.location_name}): "
                f"{signals.weather.temperature_c:.0f}°C, {signals.weather.condition_en}."
            )
    return "\n".join(lines)


async def build_scheduling_context(
    *,
    question: str,
    ctx: AnswerContext,
    weather_client: _WeatherFetcher | None,
) -> str | None:
    """Return a scheduling-context block, or ``None`` when there is no intent.

    Datetime and holiday signals are always included (cheap, local). Weather is
    fetched best-effort only when a ``weather_client`` is available, and any
    failure simply omits the weather line.
    """
    normalized = get_russian_normalizer().normalize(question)
    if not has_scheduling_intent(normalized):
        return None
    in_russian = ctx.language != "en"
    weather = (
        await _fetch_weather(normalized, ctx, weather_client)
        if weather_client is not None
        else None
    )
    signals = _collect_signals(
        normalized, ctx, in_russian=in_russian, weather=weather
    )
    return _format_block(signals, in_russian=in_russian)
