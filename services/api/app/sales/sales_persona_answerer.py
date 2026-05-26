"""SalesPersonaAnswerer — greeting + scoping stages (Story 12.03).

Activation gate (always-on, cheap, first):
  1. Existing non-dormant state → resume in that stage.
  2. No state + sales intent → enter the greeting stage.
  3. Otherwise → `_skip("not_sales_intent")` and fall through.

Stages implemented in this story:
  * `new` → greeting → transition to `scoping`.
  * `scoping` → ask the next missing intent field. Once all five fields
    are populated, transition the state to `pitching` and return the
    LLM's last reply; subsequent `pitching` turns `_skip` with
    `stage_not_implemented_yet` (later stories take over).

Pipeline wiring lives in story 12.09; this module is constructed in
`main.py` but not yet inserted into `AnswerPipeline`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.sales.intent import Intent, intent_merge
from services.api.app.sales.russian_sales_intent import is_sales_intent

logger = logging.getLogger(__name__)

NAME = "sales_persona"
STAGE_NEW = "new"
STAGE_SCOPING = "scoping"
STAGE_PITCHING = "pitching"
STAGE_DORMANT = "dormant"

_HANDLED_STAGES: frozenset[str] = frozenset({STAGE_NEW, STAGE_SCOPING})
_DEFERRED_STAGES: frozenset[str] = frozenset(
    {STAGE_PITCHING, "pricing", "proposing", "closing"}
)

_PROMPTS_DIR = Path(__file__).resolve().parent / "system_prompts"
_GREETING_PROMPT_PATH = _PROMPTS_DIR / "sales_greeting.txt"
_SCOPING_PROMPT_PATH = _PROMPTS_DIR / "sales_scoping.txt"


def _read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_GREETING_PROMPT_TEMPLATE = _read_prompt(_GREETING_PROMPT_PATH)
_SCOPING_PROMPT_TEMPLATE = _read_prompt(_SCOPING_PROMPT_PATH)


class LlmSchemaViolation(Exception):
    """Raised when the LLM's JSON-out response fails the structured schema."""


class _Normalizer(Protocol):
    def lemmas(self, text: str) -> list[str]: ...


class _StateRepo(Protocol):
    def get(self, chat_id: int) -> dict[str, Any] | None: ...

    def upsert(self, **kwargs: Any) -> None: ...


class _ServicesRepo(Protocol):
    def count_active(self, *, project_id: int) -> int: ...


class _OpenRouter(Protocol):
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
    ) -> dict[str, Any]: ...


def _skip(reason: str) -> AnswerResult:
    return AnswerResult(handled=False, metadata={"skip_reason": reason})


def _validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Enforce the JSON-out schema. Raises `LlmSchemaViolation` on failure."""
    if not isinstance(payload, dict):
        raise LlmSchemaViolation("payload is not a dict")
    extracted = payload.get("extracted_fields", {})
    if not isinstance(extracted, dict):
        raise LlmSchemaViolation("extracted_fields is not a dict")
    next_question = payload.get("next_question")
    if not isinstance(next_question, str):
        raise LlmSchemaViolation("next_question missing or not a string")
    return extracted, next_question


def _format_known_fields(intent: Intent) -> str:
    items = []
    for name, value in intent.to_dict().items():
        if value is not None:
            items.append(f"- {name}: {value}")
    return "\n".join(items) if items else "(пока ничего)"


def _format_missing_fields(intent: Intent) -> str:
    missing = intent.missing_fields()
    return "\n".join(f"- {name}" for name in missing) if missing else "(все собраны)"


def _build_greeting_prompt(*, persona: str) -> str:
    return _GREETING_PROMPT_TEMPLATE.format(persona=persona)


def _build_scoping_prompt(*, persona: str, intent: Intent) -> str:
    return _SCOPING_PROMPT_TEMPLATE.format(
        persona=persona,
        known_fields=_format_known_fields(intent),
        missing_fields=_format_missing_fields(intent),
    )


class SalesPersonaAnswerer:
    name = NAME

    def __init__(
        self,
        *,
        state_repo: _StateRepo,
        services_repo: _ServicesRepo,
        openrouter: _OpenRouter,
        normalizer: _Normalizer,
        clock: Callable[[], datetime],
        bot_persona_getter: Callable[[], str],
    ) -> None:
        self._state_repo = state_repo
        self._services_repo = services_repo
        self._openrouter = openrouter
        self._normalizer = normalizer
        self._clock = clock
        self._persona_getter = bot_persona_getter

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        if ctx.chat_id is None:
            return _skip("no_chat_id")

        state = await asyncio.to_thread(self._state_repo.get, ctx.chat_id)

        if state is None:
            if not is_sales_intent(question, normalizer=self._normalizer):
                return _skip("not_sales_intent")
            return await self._handle_greeting(question=question, ctx=ctx)

        current_stage = str(state.get("current_stage") or STAGE_NEW)
        if current_stage == STAGE_DORMANT:
            if not is_sales_intent(question, normalizer=self._normalizer):
                return _skip("not_sales_intent")
            return await self._handle_greeting(question=question, ctx=ctx)

        if current_stage in _DEFERRED_STAGES:
            return _skip("stage_not_implemented_yet")

        if current_stage == STAGE_SCOPING:
            return await self._handle_scoping(
                question=question, ctx=ctx, state=state
            )

        if current_stage == STAGE_NEW:
            return await self._handle_greeting(question=question, ctx=ctx)

        # Unknown / future stage value — defer to downstream answerers.
        return _skip("stage_not_implemented_yet")

    async def _handle_greeting(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        persona = self._persona_getter()
        system = _build_greeting_prompt(persona=persona)
        user = f"Сообщение клиента:\n{question}"

        try:
            payload = await self._openrouter.complete_json(
                system=system, user=user
            )
            extracted, next_question = _validate_payload(payload)
        except LlmSchemaViolation as exc:
            logger.warning(
                "sales_llm_schema_violation",
                extra={
                    "trace_id": ctx.trace_id,
                    "stage": STAGE_NEW,
                    "reason": str(exc),
                },
            )
            return _skip("llm_schema_violation")

        merged = intent_merge(Intent(), extracted)
        # Greeting always transitions into scoping. Even if the customer
        # already supplied every field in the opener (unlikely), the next
        # turn handles the pitching transition cleanly.
        stage_after = STAGE_SCOPING
        await self._persist(
            ctx=ctx,
            current_stage=stage_after,
            intent=merged,
        )
        logger.info(
            "sales_answerer_handled",
            extra={
                "trace_id": ctx.trace_id,
                "stage_before": STAGE_NEW,
                "stage_after": stage_after,
                "fields_extracted": sorted(
                    name
                    for name, value in merged.to_dict().items()
                    if value is not None
                ),
            },
        )
        return AnswerResult(
            handled=True,
            text=next_question,
            metadata={
                "answerer": NAME,
                "stage_before": STAGE_NEW,
                "stage_after": stage_after,
            },
        )

    async def _handle_scoping(
        self,
        *,
        question: str,
        ctx: AnswerContext,
        state: dict[str, Any],
    ) -> AnswerResult:
        persona = self._persona_getter()
        existing_intent = Intent.from_dict(state.get("collected_intent") or {})
        system = _build_scoping_prompt(persona=persona, intent=existing_intent)
        user = f"Сообщение клиента:\n{question}"

        try:
            payload = await self._openrouter.complete_json(
                system=system, user=user
            )
            extracted, next_question = _validate_payload(payload)
        except LlmSchemaViolation as exc:
            logger.warning(
                "sales_llm_schema_violation",
                extra={
                    "trace_id": ctx.trace_id,
                    "stage": STAGE_SCOPING,
                    "reason": str(exc),
                },
            )
            return _skip("llm_schema_violation")

        merged = intent_merge(existing_intent, extracted)
        stage_after = STAGE_PITCHING if merged.is_complete() else STAGE_SCOPING
        await self._persist(
            ctx=ctx,
            current_stage=stage_after,
            intent=merged,
        )
        logger.info(
            "sales_answerer_handled",
            extra={
                "trace_id": ctx.trace_id,
                "stage_before": STAGE_SCOPING,
                "stage_after": stage_after,
                "fields_extracted": sorted(
                    name
                    for name, value in extracted.items()
                    if value is not None
                ),
            },
        )
        return AnswerResult(
            handled=True,
            text=next_question,
            metadata={
                "answerer": NAME,
                "stage_before": STAGE_SCOPING,
                "stage_after": stage_after,
            },
        )

    async def _persist(
        self,
        *,
        ctx: AnswerContext,
        current_stage: str,
        intent: Intent,
    ) -> None:
        now = self._clock()
        await asyncio.to_thread(
            lambda: self._state_repo.upsert(
                chat_id=int(ctx.chat_id),  # type: ignore[arg-type]
                project_id=int(ctx.project_id or 0),
                current_stage=current_stage,
                collected_intent=intent.to_dict(),
                last_bot_msg_at=now,
                now=now,
            )
        )


__all__ = [
    "LlmSchemaViolation",
    "NAME",
    "SalesPersonaAnswerer",
]
