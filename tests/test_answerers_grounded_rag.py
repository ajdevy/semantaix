from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.grounded_rag import GroundedRagAnswerer
from services.api.app.openrouter_client import GroundingVerdict
from services.api.app.rag import RagChunk


def _ctx(*, threshold: float = 0.6) -> AnswerContext:
    return AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-1",
        now=datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        grounding_threshold=threshold,
    )


def _chunks(score: float) -> list[RagChunk]:
    return [
        RagChunk(
            id=1, source_id="kb-1",
            chunk_text="Возврат денег занимает пять рабочих дней",
            score=score,
        )
    ]


class _FakeRag:
    def __init__(self, items: list[RagChunk]) -> None:
        self._items = items
        self.calls: list[str] = []

    def retrieve(self, *, query: str, limit: int = 3) -> list[RagChunk]:
        self.calls.append(query)
        return list(self._items)


def _fake_llm(
    *,
    answer: str = "Ответ из сниппета.",
    verdict_label: str = "GROUNDED",
    verdict_reason: str = "matches snippet",
):
    llm = AsyncMock()
    llm.answer_grounded = AsyncMock(return_value=answer)
    llm.verify_grounding = AsyncMock(
        return_value=GroundingVerdict(label=verdict_label, reason=verdict_reason)
    )
    return llm


@pytest.mark.asyncio
async def test_strong_retrieval_grounded_verifier_delivers_answer():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(
        answer="Возврат занимает пять рабочих дней."
    )
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(
        question="когда придёт мой возврат?", ctx=_ctx()
    )
    assert result.handled is True
    assert result.response_mode == "grounded_rag"
    assert "пять рабочих дней" in result.text
    assert result.metadata["verifier"] == "matches snippet"
    assert result.metadata["retrieval"][0]["source_id"] == "kb-1"


@pytest.mark.asyncio
async def test_weak_retrieval_falls_through():
    rag = _FakeRag(_chunks(score=0.2))
    llm = _fake_llm()
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.answer_grounded.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_retrieval_falls_through():
    rag = _FakeRag([])
    llm = _fake_llm()
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.answer_grounded.assert_not_awaited()


@pytest.mark.asyncio
async def test_sentinel_response_escalates():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="ESCALATE_TO_HUMAN")
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.verify_grounding.assert_not_awaited()


@pytest.mark.asyncio
async def test_verifier_not_grounded_escalates():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(verdict_label="NOT_GROUNDED", verdict_reason="hallucination")
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_guardrail_hedge_escalates_even_when_verifier_grounded():
    rag = _FakeRag(_chunks(score=0.9))
    # Hedging phrase will trigger evaluate_suggestion -> low_confidence.
    llm = _fake_llm(answer="Я не знаю точного ответа.")
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_profane_llm_output_escalates():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="Полный пиздец, ничего не работает у нас.")
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_llm_generator_exception_falls_through():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm()
    llm.answer_grounded = AsyncMock(side_effect=RuntimeError("boom"))
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False


@pytest.mark.asyncio
async def test_llm_verifier_exception_falls_through():
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm()
    llm.verify_grounding = AsyncMock(side_effect=RuntimeError("verify failed"))
    answerer = GroundedRagAnswerer(rag_repository=rag, openrouter_client=llm)
    result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
