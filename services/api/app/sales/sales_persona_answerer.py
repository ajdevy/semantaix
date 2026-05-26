"""SalesPersonaAnswerer — greeting, scoping, and conversational asides.

Activation gate (always-on, cheap, first):
  1. Existing non-dormant state → resume in that stage.
  2. No state + sales intent → enter the greeting stage.
  3. Otherwise → `_skip("not_sales_intent")` and fall through.

Stages implemented:
  * `new` → greeting → transition to `scoping` (Story 12.03).
  * `scoping` → ask the next missing intent field (Story 12.03).
  * Mid-funnel asides (Story 12.06) handled inline in any active stage:
      - ``catalog_ask`` → list the project's active service names.
      - ``concept_ask`` → return the operator's description verbatim,
        fall back to a RAG-grounded one-liner, or escalate.

Pipeline wiring lives in story 12.09; this module is constructed in
`main.py` but not yet inserted into `AnswerPipeline`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.rag import RagChunk
from services.api.app.sales.intent import Intent, intent_merge
from services.api.app.sales.russian_sales_intent import is_sales_intent
from services.api.app.sales.turn_intent import classify_turn

logger = logging.getLogger(__name__)

FOLLOWUP_DELAY = timedelta(hours=24)

NAME = "sales_persona"
STAGE_NEW = "new"
STAGE_SCOPING = "scoping"
STAGE_PITCHING = "pitching"
STAGE_DORMANT = "dormant"

_HANDLED_STAGES: frozenset[str] = frozenset({STAGE_NEW, STAGE_SCOPING})
_DEFERRED_STAGES: frozenset[str] = frozenset(
    {STAGE_PITCHING, "pricing", "proposing", "closing"}
)
# Stages where mid-funnel asides (catalog / concept) are intercepted before
# the stage handler. Greeting is excluded: a brand-new chat hits the greeting
# branch first and the sales-intent gate already covers catalog phrases.
_ASIDE_INTERCEPT_STAGES: frozenset[str] = (
    frozenset({STAGE_SCOPING}) | _DEFERRED_STAGES
)

_PROMPTS_DIR = Path(__file__).resolve().parent / "system_prompts"
_GREETING_PROMPT_PATH = _PROMPTS_DIR / "sales_greeting.txt"
_SCOPING_PROMPT_PATH = _PROMPTS_DIR / "sales_scoping.txt"
_CATALOG_PROMPT_PATH = _PROMPTS_DIR / "sales_catalog.txt"
_CONCEPT_RAG_PROMPT_PATH = _PROMPTS_DIR / "sales_concept_rag.txt"


def _read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_GREETING_PROMPT_TEMPLATE = _read_prompt(_GREETING_PROMPT_PATH)
_SCOPING_PROMPT_TEMPLATE = _read_prompt(_SCOPING_PROMPT_PATH)
_CATALOG_PROMPT_TEMPLATE = _read_prompt(_CATALOG_PROMPT_PATH)
_CONCEPT_RAG_PROMPT_TEMPLATE = _read_prompt(_CONCEPT_RAG_PROMPT_PATH)


class LlmSchemaViolation(Exception):
    """Raised when the LLM's JSON-out response fails the structured schema."""


class _Normalizer(Protocol):
    def lemmas(self, text: str) -> list[str]: ...


class _StateRepo(Protocol):
    def get(self, chat_id: int) -> dict[str, Any] | None: ...

    def upsert(self, **kwargs: Any) -> None: ...


class _ServiceRow(Protocol):
    """Minimal duck-type for a service row used by the catalog/concept asides."""

    name: str
    description: str | None


class _ServicesRepo(Protocol):
    def count_active(self, *, project_id: int) -> int: ...

    def list_for_project(
        self, *, project_id: int
    ) -> list[_ServiceRow]: ...

    def get_by_name(
        self, *, project_id: int, name: str
    ) -> _ServiceRow | None: ...


class _RagRetriever(Protocol):
    def retrieve(
        self,
        *,
        query: str,
        limit: int = 3,
        project_id: int | None = None,
    ) -> list[RagChunk]: ...


class _FollowupRepo(Protocol):
    def enqueue(
        self,
        *,
        chat_id: int,
        project_id: int,
        fire_at: datetime,
        now: datetime,
    ) -> int: ...


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
        rag_retriever: _RagRetriever | None = None,
        grounding_threshold_getter: Callable[[], float] | None = None,
        followup_repo: _FollowupRepo | None = None,
    ) -> None:
        self._state_repo = state_repo
        self._services_repo = services_repo
        self._openrouter = openrouter
        self._normalizer = normalizer
        self._clock = clock
        self._persona_getter = bot_persona_getter
        self._rag = rag_retriever
        self._grounding_threshold_getter = grounding_threshold_getter
        self._followup_repo = followup_repo

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        result = await self._dispatch(question=question, ctx=ctx)
        if result.handled and ctx.chat_id is not None:
            await self._enqueue_followup(ctx=ctx)
        return result

    async def _dispatch(
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

        # Story 12.06 — intercept mid-funnel conversational asides BEFORE the
        # deferred-stage skip so a pitching/pricing customer can still ask
        # "что у вас есть?" or "что такое X?" without losing funnel state.
        if current_stage in _ASIDE_INTERCEPT_STAGES:
            aside = await self._maybe_handle_aside(
                question=question, ctx=ctx, state=state
            )
            if aside is not None:
                return aside

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

    async def _enqueue_followup(self, *, ctx: AnswerContext) -> None:
        """Schedule one nudge T+24h after every successful sales turn.

        Re-enqueueing replaces any prior ``scheduled`` row for the chat —
        the queue keeps exactly one outstanding nudge per silent customer.
        """
        if self._followup_repo is None:
            return
        now = self._clock()
        try:
            await asyncio.to_thread(
                self._followup_repo.enqueue,
                chat_id=int(ctx.chat_id),  # type: ignore[arg-type]
                project_id=int(ctx.project_id or 0),
                fire_at=now + FOLLOWUP_DELAY,
                now=now,
            )
        except Exception as exc:  # defensive — never break the answer path
            logger.warning(
                "sales_followup_enqueue_failed",
                extra={
                    "trace_id": ctx.trace_id,
                    "chat_id": ctx.chat_id,
                    "error": repr(exc),
                },
            )

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

    async def _maybe_handle_aside(
        self,
        *,
        question: str,
        ctx: AnswerContext,
        state: dict[str, Any],
    ) -> AnswerResult | None:
        """Run the per-turn classifier; route catalog/concept inline.

        Returns ``None`` when the turn is not an aside — the caller then
        continues into the stage-specific handler. Funnel state is never
        mutated by this method.
        """
        turn_intent = classify_turn(question, normalizer=self._normalizer)
        if turn_intent.kind == "catalog_ask":
            return await self._handle_catalog_ask(
                question=question, ctx=ctx, state=state
            )
        if turn_intent.kind == "concept_ask":
            return await self._handle_concept_ask(
                question=question,
                ctx=ctx,
                state=state,
                term=turn_intent.term or "",
            )
        return None

    async def _handle_catalog_ask(
        self,
        *,
        question: str,
        ctx: AnswerContext,
        state: dict[str, Any],
    ) -> AnswerResult:
        project_id = int(ctx.project_id or 0)
        services = await asyncio.to_thread(
            self._services_repo.list_for_project, project_id=project_id
        )
        active_names = [
            row.name.strip()
            for row in services
            if getattr(row, "name", None) and row.name.strip()
        ]
        if not active_names:
            logger.info(
                "sales_catalog_ask_empty",
                extra={
                    "trace_id": ctx.trace_id,
                    "project_id": project_id,
                },
            )
            return _skip("no_services")

        names_block = "\n".join(f"• {name}" for name in active_names)
        text = _CATALOG_PROMPT_TEMPLATE.format(names_block=names_block).strip()
        logger.info(
            "sales_answerer_handled",
            extra={
                "trace_id": ctx.trace_id,
                "stage_before": str(state.get("current_stage") or ""),
                "stage_after": str(state.get("current_stage") or ""),
                "sales_turn_kind": "catalog",
                "service_count": len(active_names),
            },
        )
        return AnswerResult(
            handled=True,
            text=text,
            metadata={
                "answerer": NAME,
                "stage_before": str(state.get("current_stage") or ""),
                "stage_after": str(state.get("current_stage") or ""),
                "sales_turn_kind": "catalog",
            },
        )

    async def _handle_concept_ask(
        self,
        *,
        question: str,
        ctx: AnswerContext,
        state: dict[str, Any],
        term: str,
    ) -> AnswerResult:
        # `classify_turn` already downgrades empty/punctuation-only terms
        # to ``other``, so by the time we get here the term is non-empty.
        term_clean = term.strip()
        project_id = int(ctx.project_id or 0)
        current_stage = str(state.get("current_stage") or "")
        service = await self._lookup_service_for_term(
            project_id=project_id, term=term_clean
        )
        if service is not None and (service.description or "").strip():
            description = (service.description or "").strip()
            logger.info(
                "sales_answerer_handled",
                extra={
                    "trace_id": ctx.trace_id,
                    "stage_before": current_stage,
                    "stage_after": current_stage,
                    "sales_turn_kind": "concept_op_desc",
                    "service_name": service.name,
                },
            )
            return AnswerResult(
                handled=True,
                text=description,
                metadata={
                    "answerer": NAME,
                    "stage_before": current_stage,
                    "stage_after": current_stage,
                    "sales_turn_kind": "concept_op_desc",
                    "service_name": service.name,
                },
            )

        return await self._answer_concept_via_rag(
            term=term_clean,
            ctx=ctx,
            current_stage=current_stage,
        )

    async def _lookup_service_for_term(
        self, *, project_id: int, term: str
    ) -> _ServiceRow | None:
        """Find a service whose name matches the customer's term.

        Two-pass match: exact case-insensitive first (cheap), then lemma-set
        equality across all services (handles Russian inflection like
        "Медовеевку Лайт" → "Медовеевка Лайт"). The lemma fallback only
        triggers when the exact lookup misses, so calls cost the same as
        before for the common nominative-case path.
        """
        exact = await asyncio.to_thread(
            self._services_repo.get_by_name,
            project_id=project_id,
            name=term,
        )
        if exact is not None:
            return exact

        # `classify_turn` filters all-punctuation terms, so the lemmatised
        # set is non-empty in practice; the loop below tolerates an empty
        # set anyway (every comparison would fail and we return ``None``).
        term_lemmas = set(self._normalizer.lemmas(term))
        services = await asyncio.to_thread(
            self._services_repo.list_for_project, project_id=project_id
        )
        for row in services:
            name = getattr(row, "name", None) or ""
            name_lemmas = set(self._normalizer.lemmas(name))
            if name_lemmas and name_lemmas == term_lemmas:
                return row
        return None

    async def _answer_concept_via_rag(
        self,
        *,
        term: str,
        ctx: AnswerContext,
        current_stage: str,
    ) -> AnswerResult:
        threshold = self._resolve_grounding_threshold(ctx)
        chunks: list[RagChunk] = []
        if self._rag is not None:
            chunks = await asyncio.to_thread(
                self._rag.retrieve,
                query=f"{term} определение",
                limit=3,
                project_id=ctx.project_id,
            )
        if chunks and chunks[0].score >= threshold:
            top = chunks[0]
            persona = self._persona_getter()
            system = _CONCEPT_RAG_PROMPT_TEMPLATE.format(
                persona=persona, term=term, chunk_text=top.chunk_text
            )
            user = f"Клиент спросил: «что такое {term}?»"
            try:
                payload = await self._openrouter.complete_json(
                    system=system, user=user
                )
            except Exception as exc:  # defensive — LLM transport failure
                logger.warning(
                    "sales_concept_rag_llm_error",
                    extra={
                        "trace_id": ctx.trace_id,
                        "term": term,
                        "error": repr(exc),
                    },
                )
                return self._escalate_concept_unknown(
                    term=term, ctx=ctx, current_stage=current_stage
                )
            text = ""
            if isinstance(payload, dict):
                raw = payload.get("text") or payload.get("next_question")
                if isinstance(raw, str):
                    text = raw.strip()
            if not text:
                logger.warning(
                    "sales_concept_rag_invalid_payload",
                    extra={
                        "trace_id": ctx.trace_id,
                        "term": term,
                    },
                )
                return self._escalate_concept_unknown(
                    term=term, ctx=ctx, current_stage=current_stage
                )
            logger.info(
                "sales_answerer_handled",
                extra={
                    "trace_id": ctx.trace_id,
                    "stage_before": current_stage,
                    "stage_after": current_stage,
                    "sales_turn_kind": "concept_rag",
                    "rag_top_score": top.score,
                },
            )
            return AnswerResult(
                handled=True,
                text=text,
                metadata={
                    "answerer": NAME,
                    "stage_before": current_stage,
                    "stage_after": current_stage,
                    "sales_turn_kind": "concept_rag",
                    "rag_top_score": top.score,
                },
            )
        return self._escalate_concept_unknown(
            term=term, ctx=ctx, current_stage=current_stage
        )

    def _escalate_concept_unknown(
        self,
        *,
        term: str,
        ctx: AnswerContext,
        current_stage: str,
    ) -> AnswerResult:
        logger.info(
            "sales_concept_escalation",
            extra={
                "trace_id": ctx.trace_id,
                "term": term,
                "stage": current_stage,
                "reason": "concept_unknown",
            },
        )
        return AnswerResult(
            handled=False,
            metadata={
                "skip_reason": "concept_unknown",
                "sales_turn_kind": "concept_unknown",
                "concept_term": term,
            },
        )

    def _resolve_grounding_threshold(self, ctx: AnswerContext) -> float:
        if self._grounding_threshold_getter is not None:
            try:
                return float(self._grounding_threshold_getter())
            except (TypeError, ValueError):
                return ctx.grounding_threshold
        return ctx.grounding_threshold

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
