from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.grounded_rag import GroundedRagAnswerer
from services.api.app.openrouter_client import GroundingVerdict
from services.api.app.project_prompts import ProjectPromptRepository
from services.api.app.rag import RagChunk


@pytest.fixture
def prompts(tmp_path) -> ProjectPromptRepository:
    return ProjectPromptRepository(str(tmp_path / "prompts.sqlite3"))


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

    def retrieve(
        self,
        *,
        query: str,
        limit: int = 3,
        project_id: int | None = None,
    ) -> list[RagChunk]:
        self.calls.append(query)
        self.last_project_id = project_id
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


def _assert_skip_log(
    caplog: pytest.LogCaptureFixture,
    *,
    reason: str,
    question: str,
    retrieved_count: int,
    top_score: float | None,
) -> logging.LogRecord:
    records = [
        r
        for r in caplog.records
        if r.message == "grounded_rag_skipped"
        and getattr(r, "reason", None) == reason
    ]
    assert records, f"expected grounded_rag_skipped log with reason={reason}"
    record = records[-1]
    assert record.query == question
    assert record.threshold == 0.6
    assert record.retrieved_count == retrieved_count
    assert record.top_score == top_score
    assert record.trace_id == "t-1"
    assert record.chunk_confidential_flags == [False] * retrieved_count
    assert record.chunk_project_ids == [None] * retrieved_count
    return record


@pytest.mark.asyncio
async def test_strong_retrieval_grounded_verifier_delivers_answer(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(
        answer="Возврат занимает пять рабочих дней."
    )
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(
        logging.INFO, logger="services.api.app.answerers.grounded_rag"
    ):
        result = await answerer.try_answer(
            question="когда придёт мой возврат?", ctx=_ctx()
        )
    assert result.handled is True
    assert result.response_mode == "grounded_rag"
    assert "пять рабочих дней" in result.text
    assert result.metadata["verifier"] == "matches snippet"
    assert result.metadata["retrieval"][0]["source_id"] == "kb-1"
    # Persona name must reach the LLM call so it speaks in-character.
    assert llm.answer_grounded.await_args.kwargs["persona_first_name"] == "Анна"
    assert llm.answer_grounded.await_args.kwargs["persona_last_name"] == "Иванова"
    # Each pipeline stage emits a structured event.
    events = [r.message for r in caplog.records]
    for expected in (
        "grounded_rag_pipeline_entry",
        "grounded_rag_llm_request",
        "grounded_rag_llm_response",
        "grounded_rag_verifier_result",
        "grounded_rag_guardrail_result",
        "grounded_rag_profanity_result",
        "grounded_rag_delivered",
    ):
        assert expected in events, f"missing event {expected}"
    entry = next(
        r for r in caplog.records if r.message == "grounded_rag_pipeline_entry"
    )
    assert entry.chunk_confidential_flags == [False]
    assert entry.chunk_project_ids == [None]
    assert entry.top_score == 0.9
    delivered = next(
        r for r in caplog.records if r.message == "grounded_rag_delivered"
    )
    assert delivered.retrieval_source_ids == ["kb-1"]
    assert delivered.guardrail_score == 0.95


@pytest.mark.asyncio
async def test_scheduling_intent_passes_context_to_llm_calls(prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="Доставим ваш заказ завтра в течение рабочего дня.")
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
        weather_client=None,
    )
    result = await answerer.try_answer(
        question="можете доставить заказ завтра?", ctx=_ctx()
    )
    assert result.handled is True
    answer_ctx = llm.answer_grounded.await_args.kwargs["scheduling_context"]
    verify_ctx = llm.verify_grounding.await_args.kwargs["scheduling_context"]
    assert answer_ctx is not None
    assert "Справочный контекст для планирования" in answer_ctx
    assert verify_ctx == answer_ctx


@pytest.mark.asyncio
async def test_non_scheduling_question_passes_no_context(prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm()
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    result = await answerer.try_answer(
        question="когда придёт мой возврат?", ctx=_ctx()
    )
    assert result.handled is True
    assert llm.answer_grounded.await_args.kwargs["scheduling_context"] is None
    assert llm.verify_grounding.await_args.kwargs["scheduling_context"] is None


@pytest.mark.asyncio
async def test_weak_retrieval_falls_through(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.2))
    llm = _fake_llm()
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.answer_grounded.assert_not_awaited()
    record = _assert_skip_log(
        caplog,
        reason="below_threshold",
        question="q",
        retrieved_count=1,
        top_score=0.2,
    )
    assert record.chunk_source_ids == ["kb-1"]


@pytest.mark.asyncio
async def test_empty_retrieval_falls_through(caplog, prompts):
    rag = _FakeRag([])
    llm = _fake_llm()
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.answer_grounded.assert_not_awaited()
    _assert_skip_log(
        caplog,
        reason="no_chunks",
        question="q",
        retrieved_count=0,
        top_score=None,
    )


@pytest.mark.asyncio
async def test_sentinel_response_escalates(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="ESCALATE_TO_HUMAN")
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    llm.verify_grounding.assert_not_awaited()
    _assert_skip_log(
        caplog,
        reason="escalate_sentinel",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )


@pytest.mark.asyncio
async def test_verifier_not_grounded_escalates(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(verdict_label="NOT_GROUNDED", verdict_reason="hallucination")
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    record = _assert_skip_log(
        caplog,
        reason="verifier_not_grounded",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )
    assert record.verdict_label == "NOT_GROUNDED"
    assert record.verdict_reason == "hallucination"


@pytest.mark.asyncio
async def test_guardrail_hedge_escalates_even_when_verifier_grounded(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    # Hedging phrase will trigger evaluate_suggestion -> low_confidence.
    llm = _fake_llm(answer="Я не знаю точного ответа.")
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    record = _assert_skip_log(
        caplog,
        reason="guardrail_invalid",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )
    assert hasattr(record, "guardrail_score")
    assert isinstance(record.guardrail_failure_reasons, list)
    assert record.guardrail_failure_reasons, "expected at least one reason"
    # The guardrail result event also fires with the same reasons.
    guardrail_events = [
        r for r in caplog.records if r.message == "grounded_rag_guardrail_result"
    ]
    assert guardrail_events and guardrail_events[-1].valid is False
    assert guardrail_events[-1].failure_reasons == record.guardrail_failure_reasons


@pytest.mark.asyncio
async def test_profane_llm_output_escalates(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="Полный пиздец, ничего не работает у нас.")
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    _assert_skip_log(
        caplog,
        reason="profanity_detected",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )


@pytest.mark.asyncio
async def test_llm_generator_exception_falls_through(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm()
    llm.answer_grounded = AsyncMock(side_effect=RuntimeError("boom"))
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    record = _assert_skip_log(
        caplog,
        reason="llm_generator_error",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )
    assert "boom" in record.error


@pytest.mark.asyncio
async def test_llm_verifier_exception_falls_through(caplog, prompts):
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm()
    llm.verify_grounding = AsyncMock(side_effect=RuntimeError("verify failed"))
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    with caplog.at_level(logging.INFO, logger="services.api.app.answerers.grounded_rag"):
        result = await answerer.try_answer(question="q", ctx=_ctx())
    assert result.handled is False
    record = _assert_skip_log(
        caplog,
        reason="verifier_error",
        question="q",
        retrieved_count=1,
        top_score=0.9,
    )
    assert "verify failed" in record.error


@pytest.mark.asyncio
async def test_per_project_prompt_overrides_reach_llm_and_guardrails(
    caplog, prompts
):
    """When the project has overrides, GroundedRagAnswerer feeds them through
    to the LLM (system prompts) and to the guardrail/profanity checks."""
    rag = _FakeRag(_chunks(score=0.9))
    llm = _fake_llm(answer="свеженькое мнение.")
    prompts.set(
        project_id=7,
        prompt_name="grounding_system",
        value="custom-{name}-{today_iso}",
        edited_by="@admin",
    )
    prompts.set(
        project_id=7,
        prompt_name="verifier_system",
        value="custom verifier",
        edited_by="@admin",
    )
    prompts.set(
        project_id=7,
        prompt_name="guardrail_hedges",
        value="мнение",
        edited_by="@admin",
    )
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=llm,
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=prompts,
    )
    ctx = AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t-prompt",
        now=datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        grounding_threshold=0.6,
        project_id=7,
    )
    result = await answerer.try_answer(
        question="что вы думаете?", ctx=ctx
    )
    # Override propagated to OpenRouter calls.
    assert llm.answer_grounded.await_args.kwargs["system_prompt_template"] == (
        "custom-{name}-{today_iso}"
    )
    assert llm.verify_grounding.await_args.kwargs["system_prompt"] == (
        "custom verifier"
    )
    # The custom hedge "мнение" appears in the answer, so the guardrail
    # rejects what the verifier accepted — the answerer must fall through.
    assert result.handled is False
