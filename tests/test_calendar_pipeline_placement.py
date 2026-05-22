"""Pipeline-placement guard for the calendar availability answerer (11.07).

Asserts the answerer sits BEFORE ``grounded_rag`` in the assembled pipeline and
that a calendar-disabled project falls through to RAG unchanged (regression).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.api.app.answerers import (
    AnswerContext,
    AnswerPipeline,
    AnswerResult,
)
from services.api.app.calendar.availability_answerer import (
    CalendarAvailabilityAnswerer,
)
from services.api.app.calendar.clarify_state_repository import (
    CalendarClarifyStateRepository,
)
from services.api.app.calendar.settings_repository import CalendarSettingsRepository
from services.api.app.russian_text import get_russian_normalizer


def test_main_pipeline_places_calendar_before_grounded_rag() -> None:
    from services.api.app import main as api_main

    names = [a.name for a in api_main.answer_pipeline.answerers]
    assert "calendar_availability" in names
    assert "grounded_rag" in names
    assert names.index("calendar_availability") < names.index("grounded_rag")


class _SpyRagAnswerer:
    name = "grounded_rag"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        self.calls.append(question)
        return AnswerResult(handled=True, text="RAG", response_mode="grounded_rag")


@pytest.mark.asyncio
async def test_disabled_project_falls_through_to_rag_unchanged(
    tmp_path: Path,
) -> None:
    # An empty calendar DB -> project 11 is disabled (default-off).
    settings_repo = CalendarSettingsRepository(
        db_path=str(tmp_path / "calendar.sqlite3")
    )
    clarify = CalendarClarifyStateRepository(
        db_path=str(tmp_path / "clarify.sqlite3")
    )
    calendar = CalendarAvailabilityAnswerer(
        settings_repo=settings_repo,
        token_provider=AsyncMock(),
        freebusy_client=AsyncMock(),
        normalizer=get_russian_normalizer(),
        clarify_store=clarify,
        operator_chat_resolver=lambda operator: 1,
    )
    rag = _SpyRagAnswerer()
    pipeline = AnswerPipeline([calendar, rag])

    ctx = AnswerContext(
        chat_id=42,
        customer_username="@c",
        trace_id="t-1",
        now=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
        project_id=11,
    )
    # An availability-shaped question on a DISABLED project must reach RAG,
    # exactly as it did before the calendar answerer existed.
    result = await pipeline.run(
        question="можно записаться на маникюр в субботу в 15:00?", ctx=ctx
    )
    assert result.handled is True
    assert result.response_mode == "grounded_rag"
    assert result.metadata["answerer"] == "grounded_rag"
    assert rag.calls == ["можно записаться на маникюр в субботу в 15:00?"]
