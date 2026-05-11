from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.datetime_answerer import DateTimeAnswerer


def _ctx(language: str = "ru", timezone: str = "Europe/Moscow") -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
        now=datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        language=language,
        timezone=timezone,
    )


@pytest.mark.asyncio
async def test_ru_date_question_answered_in_russian():
    answerer = DateTimeAnswerer()
    result = await answerer.try_answer(question="Какое сегодня число?", ctx=_ctx())
    assert result.handled is True
    assert result.response_mode == "deterministic_datetime"
    assert "11 мая 2026" in result.text
    assert "Europe/Moscow" in result.text


@pytest.mark.asyncio
async def test_ru_time_question_answered_in_russian():
    answerer = DateTimeAnswerer()
    result = await answerer.try_answer(question="Который час?", ctx=_ctx())
    assert result.handled is True
    assert "13:00" in result.text  # 10:00 UTC + 3h


@pytest.mark.asyncio
async def test_ru_slang_time_via_normalization():
    answerer = DateTimeAnswerer()
    # "че по времени" -> normalize -> "что по времени" -> regex match
    result = await answerer.try_answer(question="че по времени?", ctx=_ctx())
    assert result.handled is True
    assert "Europe/Moscow" in result.text


@pytest.mark.asyncio
async def test_en_date_question_answered_in_english():
    answerer = DateTimeAnswerer()
    result = await answerer.try_answer(
        question="What is the date?", ctx=_ctx(language="en", timezone="UTC")
    )
    assert result.handled is True
    assert "May 11, 2026" in result.text


@pytest.mark.asyncio
async def test_no_match_returns_unhandled():
    answerer = DateTimeAnswerer()
    result = await answerer.try_answer(question="Когда придёт мой возврат?", ctx=_ctx())
    assert result.handled is False
    assert result.text is None
