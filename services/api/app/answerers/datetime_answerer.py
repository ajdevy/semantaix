from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.russian_text import get_russian_normalizer

_RU_PATTERN = re.compile(
    r"который\s+час"
    r"|сколько\s+(?:сейчас\s+)?времени"
    r"|что\s+по\s+времени"
    r"|какое\s+(?:сегодня\s+)?число"
    r"|какая\s+(?:сегодня\s+)?дата"
    r"|какой\s+(?:сегодня\s+)?день"
    r"|сегодняшняя\s+дата",
    re.IGNORECASE | re.UNICODE,
)

_EN_PATTERN = re.compile(
    r"what(?:\s+is)?\s+the\s+(?:time|date)"
    r"|today'?s\s+date"
    r"|current\s+time"
    r"|what\s+day\s+is\s+it",
    re.IGNORECASE,
)

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


class DateTimeAnswerer:
    name = "datetime"

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        normalized = get_russian_normalizer().normalize(question)
        ru_match = _RU_PATTERN.search(normalized)
        en_match = _EN_PATTERN.search(normalized)
        if not (ru_match or en_match):
            return AnswerResult(handled=False)

        tz = ZoneInfo(ctx.timezone)
        local = ctx.now.astimezone(tz)
        if ru_match:
            month = _RU_MONTHS[local.month]
            text = (
                f"Сейчас {local.strftime('%H:%M')} ({ctx.timezone}), "
                f"{local.day} {month} {local.year} г."
            )
        else:
            month = _EN_MONTHS[local.month]
            text = (
                f"It is {local.strftime('%H:%M')} ({ctx.timezone}), "
                f"{month} {local.day}, {local.year}."
            )
        return AnswerResult(
            handled=True,
            text=text,
            response_mode="deterministic_datetime",
            metadata={"timezone": ctx.timezone},
        )
