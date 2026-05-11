from __future__ import annotations

import re
from datetime import date
from zoneinfo import ZoneInfo

import holidays as _holidays_lib

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.russian_text import get_russian_normalizer

_RU_PATTERN = re.compile(
    r"праздник|выходной",
    re.IGNORECASE | re.UNICODE,
)

_EN_PATTERN = re.compile(
    r"holiday|next\s+holiday|is\s+\w+\s+a\s+holiday",
    re.IGNORECASE,
)

_RU_NEXT = re.compile(r"следующий|ближайший|скоро", re.IGNORECASE | re.UNICODE)
_RU_TODAY = re.compile(r"сегодня", re.IGNORECASE | re.UNICODE)
_EN_NEXT = re.compile(r"next", re.IGNORECASE)
_EN_TODAY = re.compile(r"today", re.IGNORECASE)


def _resolve_country_holidays(
    country_code: str, year: int, *, language: str
):
    try:
        return _holidays_lib.country_holidays(
            country_code, years=year, language=language
        )
    except (NotImplementedError, KeyError):
        return None


class HolidayAnswerer:
    name = "holiday"

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        normalized = get_russian_normalizer().normalize(question)
        ru_match = _RU_PATTERN.search(normalized)
        en_match = _EN_PATTERN.search(normalized)
        if not (ru_match or en_match):
            return AnswerResult(handled=False)

        in_russian = bool(ru_match)
        tz = ZoneInfo(ctx.timezone)
        today_local = ctx.now.astimezone(tz).date()
        ru_lang = "ru" if in_russian else "en"
        calendar = _resolve_country_holidays(
            ctx.country_code, today_local.year, language=ru_lang
        )
        if calendar is None:
            return AnswerResult(handled=False)

        ask_today = bool(_RU_TODAY.search(normalized) or _EN_TODAY.search(normalized))
        ask_next = bool(_RU_NEXT.search(normalized) or _EN_NEXT.search(normalized))

        if ask_today or not ask_next:
            todays = calendar.get(today_local)
            if todays:
                text = (
                    f"Сегодня — праздник: {todays}." if in_russian
                    else f"Today is a holiday: {todays}."
                )
                return AnswerResult(
                    handled=True,
                    text=text,
                    response_mode="deterministic_holiday",
                    metadata={"country_code": ctx.country_code, "date": str(today_local)},
                )
            if ask_today:
                text = (
                    "Сегодня не праздник." if in_russian
                    else "Today is not a holiday."
                )
                return AnswerResult(
                    handled=True,
                    text=text,
                    response_mode="deterministic_holiday",
                    metadata={"country_code": ctx.country_code, "date": str(today_local)},
                )

        next_date, next_name = _find_next_holiday(
            today_local, ctx.country_code, language=ru_lang
        )
        if next_date is None:
            return AnswerResult(handled=False)
        if in_russian:
            text = (
                f"Следующий праздник в стране {ctx.country_code}: "
                f"{next_date.strftime('%d.%m.%Y')} — {next_name}."
            )
        else:
            text = (
                f"Next holiday in {ctx.country_code}: "
                f"{next_date.strftime('%Y-%m-%d')} — {next_name}."
            )
        return AnswerResult(
            handled=True,
            text=text,
            response_mode="deterministic_holiday",
            metadata={"country_code": ctx.country_code, "date": str(next_date)},
        )


def _find_next_holiday(
    today: date, country_code: str, *, language: str
) -> tuple[date | None, str | None]:
    for year in (today.year, today.year + 1):
        calendar = _resolve_country_holidays(country_code, year, language=language)
        if calendar is None:
            return None, None
        upcoming = sorted(d for d in calendar.keys() if d > today)
        if upcoming:
            first = upcoming[0]
            return first, calendar[first]
    return None, None
