from __future__ import annotations

import logging
from typing import Any, Protocol

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.guardrails import evaluate_suggestion
from services.api.app.openrouter_client import OpenRouterClient
from services.api.app.rag import RagChunk
from services.api.app.russian_text import get_russian_normalizer

_SENTINEL = "ESCALATE_TO_HUMAN"

logger = logging.getLogger(__name__)


class _RagReader(Protocol):
    def retrieve(
        self,
        *,
        query: str,
        limit: int = 3,
        project_id: int | None = None,
    ) -> list[RagChunk]: ...


class _PersonaReader(Protocol):
    def __call__(self) -> tuple[str, str]: ...


class GroundedRagAnswerer:
    name = "grounded_rag"

    def __init__(
        self,
        *,
        rag_repository: _RagReader,
        openrouter_client: OpenRouterClient,
        persona_reader: _PersonaReader,
    ) -> None:
        self._rag = rag_repository
        self._llm = openrouter_client
        self._persona_reader = persona_reader

    async def try_answer(
        self, *, question: str, ctx: AnswerContext
    ) -> AnswerResult:
        chunks = self._rag.retrieve(
            query=question, limit=3, project_id=ctx.project_id
        )
        if not chunks:
            return self._skip(
                reason="no_chunks",
                ctx=ctx,
                question=question,
                chunks=chunks,
            )
        if chunks[0].score < ctx.grounding_threshold:
            return self._skip(
                reason="below_threshold",
                ctx=ctx,
                question=question,
                chunks=chunks,
            )

        today_iso = ctx.now.date().isoformat()
        first_name, last_name = self._persona_reader()
        try:
            answer = await self._llm.answer_grounded(
                question=question,
                snippets=chunks,
                today_iso=today_iso,
                persona_first_name=first_name,
                persona_last_name=last_name,
            )
        except Exception as exc:
            return self._skip(
                reason="llm_generator_error",
                ctx=ctx,
                question=question,
                chunks=chunks,
                error=repr(exc),
            )

        if answer.strip().upper() == _SENTINEL:
            return self._skip(
                reason="escalate_sentinel",
                ctx=ctx,
                question=question,
                chunks=chunks,
            )

        try:
            verdict = await self._llm.verify_grounding(
                question=question,
                answer=answer,
                snippets=chunks,
            )
        except Exception as exc:
            return self._skip(
                reason="verifier_error",
                ctx=ctx,
                question=question,
                chunks=chunks,
                error=repr(exc),
            )
        if verdict.label != "GROUNDED":
            return self._skip(
                reason="verifier_not_grounded",
                ctx=ctx,
                question=question,
                chunks=chunks,
                verdict_label=verdict.label,
                verdict_reason=verdict.reason,
            )

        decision = evaluate_suggestion(answer)
        if not decision.valid:
            return self._skip(
                reason="guardrail_invalid",
                ctx=ctx,
                question=question,
                chunks=chunks,
                guardrail_score=decision.score,
            )

        if get_russian_normalizer().contains_profanity(answer):
            return self._skip(
                reason="profanity_detected",
                ctx=ctx,
                question=question,
                chunks=chunks,
            )

        return AnswerResult(
            handled=True,
            text=answer.strip(),
            response_mode="grounded_rag",
            metadata={
                "retrieval": [_render_chunk_metadata(chunk) for chunk in chunks],
                "verifier": verdict.reason,
                "guardrail_score": decision.score,
            },
        )

    def _skip(
        self,
        *,
        reason: str,
        ctx: AnswerContext,
        question: str,
        chunks: list[RagChunk],
        **extra: Any,
    ) -> AnswerResult:
        payload: dict[str, Any] = {
            "trace_id": ctx.trace_id,
            "reason": reason,
            "query": question,
            "threshold": ctx.grounding_threshold,
            "retrieved_count": len(chunks),
            "top_score": chunks[0].score if chunks else None,
            "chunk_source_ids": [chunk.source_id for chunk in chunks],
        }
        payload.update(extra)
        logger.info("grounded_rag_skipped", extra=payload)
        return AnswerResult(handled=False)


def _render_chunk_metadata(chunk: RagChunk) -> dict[str, object]:
    if chunk.is_confidential:
        return {
            "source_id": "knowledge_candidate:confidential",
            "chunk_text": "[redacted]",
            "score": chunk.score,
            "is_confidential": True,
        }
    return {
        "source_id": chunk.source_id,
        "chunk_text": chunk.chunk_text,
        "score": chunk.score,
        "is_confidential": False,
    }
