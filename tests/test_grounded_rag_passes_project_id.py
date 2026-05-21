"""Verify GroundedRagAnswerer forwards AnswerContext.project_id to retrieve()."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.api.app.answerers import AnswerContext
from services.api.app.answerers.grounded_rag import GroundedRagAnswerer
from services.api.app.project_prompts import ProjectPromptRepository
from services.api.app.rag import RagChunk


class _RecordingRag:
    def __init__(self) -> None:
        self.last_project_id: int | None = "sentinel"  # type: ignore[assignment]

    def retrieve(
        self,
        *,
        query: str,
        limit: int = 3,
        project_id: int | None = None,
        catalog_mode: bool = False,
    ) -> list[RagChunk]:
        self.last_project_id = project_id
        return []


class _FakeLLM:
    pass


@pytest.mark.asyncio
async def test_grounded_rag_forwards_project_id(tmp_path):
    rag = _RecordingRag()
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=_FakeLLM(),  # type: ignore[arg-type]
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=ProjectPromptRepository(
            str(tmp_path / "prompts.sqlite3")
        ),
    )
    ctx = AnswerContext(
        chat_id=1,
        customer_username="@c",
        trace_id="t",
        now=datetime.now(UTC),
        project_id=42,
    )
    await answerer.try_answer(question="вопрос", ctx=ctx)
    assert rag.last_project_id == 42


@pytest.mark.asyncio
async def test_grounded_rag_forwards_none_when_unset(tmp_path):
    rag = _RecordingRag()
    answerer = GroundedRagAnswerer(
        rag_repository=rag,
        openrouter_client=_FakeLLM(),  # type: ignore[arg-type]
        persona_reader=lambda: ("Анна", "Иванова"),
        project_prompt_repository=ProjectPromptRepository(
            str(tmp_path / "prompts.sqlite3")
        ),
    )
    ctx = AnswerContext(
        chat_id=1, customer_username="@c", trace_id="t", now=datetime.now(UTC)
    )
    await answerer.try_answer(question="вопрос", ctx=ctx)
    assert rag.last_project_id is None
