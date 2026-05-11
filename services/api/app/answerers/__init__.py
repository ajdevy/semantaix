from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class AnswerResult:
    handled: bool
    text: str | None = None
    response_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerContext:
    chat_id: int | None
    customer_username: str | None
    trace_id: str
    now: datetime
    language: str = "ru"
    country_code: str = "RU"
    timezone: str = "Europe/Moscow"
    location: str = "Moscow"
    grounding_threshold: float = 0.6


class Answerer(Protocol):
    name: str

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult: ...


class AnswerPipeline:
    def __init__(self, answerers: list[Answerer]) -> None:
        self._answerers = list(answerers)

    @property
    def answerers(self) -> tuple[Answerer, ...]:
        return tuple(self._answerers)

    async def run(self, *, question: str, ctx: AnswerContext) -> AnswerResult:
        for answerer in self._answerers:
            result = await answerer.try_answer(question=question, ctx=ctx)
            if result.handled:
                metadata = dict(result.metadata)
                metadata.setdefault("answerer", answerer.name)
                return AnswerResult(
                    handled=True,
                    text=result.text,
                    response_mode=result.response_mode,
                    metadata=metadata,
                )
        return AnswerResult(handled=False)


__all__ = ["AnswerContext", "AnswerPipeline", "AnswerResult", "Answerer"]
