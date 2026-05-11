from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.api.app.answerers import AnswerContext, AnswerPipeline, AnswerResult


class _StubAnswerer:
    def __init__(self, name: str, handled: bool, text: str | None = None) -> None:
        self.name = name
        self._handled = handled
        self._text = text
        self.call_count = 0

    async def try_answer(self, *, question: str, ctx: AnswerContext) -> AnswerResult:
        self.call_count += 1
        if not self._handled:
            return AnswerResult(handled=False)
        return AnswerResult(
            handled=True,
            text=self._text,
            response_mode=f"stub_{self.name}",
        )


def _ctx() -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@customer",
        trace_id="t-1",
        now=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_pipeline_returns_first_handled_answer():
    a = _StubAnswerer("a", handled=False)
    b = _StubAnswerer("b", handled=True, text="hello")
    c = _StubAnswerer("c", handled=True, text="should-not-reach")
    pipeline = AnswerPipeline([a, b, c])

    result = await pipeline.run(question="anything", ctx=_ctx())

    assert result.handled is True
    assert result.text == "hello"
    assert result.response_mode == "stub_b"
    assert result.metadata["answerer"] == "b"
    assert a.call_count == 1
    assert b.call_count == 1
    assert c.call_count == 0


@pytest.mark.asyncio
async def test_pipeline_returns_unhandled_when_all_skip():
    pipeline = AnswerPipeline(
        [
            _StubAnswerer("a", handled=False),
            _StubAnswerer("b", handled=False),
        ]
    )
    result = await pipeline.run(question="anything", ctx=_ctx())
    assert result.handled is False
    assert result.text is None


@pytest.mark.asyncio
async def test_empty_pipeline_returns_unhandled():
    pipeline = AnswerPipeline([])
    result = await pipeline.run(question="anything", ctx=_ctx())
    assert result.handled is False
