from __future__ import annotations

import logging
from typing import Any, Protocol

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.guardrails import evaluate_suggestion
from services.api.app.openrouter_client import OpenRouterClient
from services.api.app.project_prompts import (
    ProjectPromptRepository,
    resolve_prompt,
    split_guardrail_lines,
)
from services.api.app.rag import RagChunk
from services.api.app.russian_text import get_russian_normalizer

_SENTINEL = "ESCALATE_TO_HUMAN"
_ANSWER_SNIPPET_MAX = 200

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
        project_prompt_repository: ProjectPromptRepository,
    ) -> None:
        self._rag = rag_repository
        self._llm = openrouter_client
        self._persona_reader = persona_reader
        self._prompts = project_prompt_repository

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

        logger.info(
            "grounded_rag_pipeline_entry",
            extra={
                "trace_id": ctx.trace_id,
                "top_score": chunks[0].score,
                "threshold": ctx.grounding_threshold,
                "chunk_source_ids": [c.source_id for c in chunks],
                "chunk_confidential_flags": [c.is_confidential for c in chunks],
                "chunk_project_ids": [c.project_id for c in chunks],
            },
        )

        today_iso = ctx.now.date().isoformat()
        first_name, last_name = self._persona_reader()
        grounding_template = resolve_prompt(
            self._prompts, ctx.project_id, "grounding_system"
        )
        verifier_prompt = resolve_prompt(
            self._prompts, ctx.project_id, "verifier_system"
        )
        hedge_lines = split_guardrail_lines(
            resolve_prompt(self._prompts, ctx.project_id, "guardrail_hedges")
        )
        policy_lines = split_guardrail_lines(
            resolve_prompt(self._prompts, ctx.project_id, "guardrail_policy")
        )
        profanity_lines = split_guardrail_lines(
            resolve_prompt(self._prompts, ctx.project_id, "guardrail_profanity")
        )
        logger.info(
            "grounded_rag_llm_request",
            extra={
                "trace_id": ctx.trace_id,
                "persona_first_name": first_name,
                "persona_last_name": last_name,
                "snippet_count": len(chunks),
                "today_iso": today_iso,
            },
        )
        try:
            answer = await self._llm.answer_grounded(
                question=question,
                snippets=chunks,
                today_iso=today_iso,
                persona_first_name=first_name,
                persona_last_name=last_name,
                system_prompt_template=grounding_template,
            )
        except Exception as exc:
            return self._skip(
                reason="llm_generator_error",
                ctx=ctx,
                question=question,
                chunks=chunks,
                error=repr(exc),
            )

        is_sentinel = answer.strip().upper() == _SENTINEL
        logger.info(
            "grounded_rag_llm_response",
            extra={
                "trace_id": ctx.trace_id,
                "answer_length": len(answer),
                "answer_snippet": answer[:_ANSWER_SNIPPET_MAX],
                "is_sentinel": is_sentinel,
            },
        )
        if is_sentinel:
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
                system_prompt=verifier_prompt,
            )
        except Exception as exc:
            return self._skip(
                reason="verifier_error",
                ctx=ctx,
                question=question,
                chunks=chunks,
                error=repr(exc),
            )
        logger.info(
            "grounded_rag_verifier_result",
            extra={
                "trace_id": ctx.trace_id,
                "verdict_label": verdict.label,
                "verdict_reason": verdict.reason,
            },
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

        decision = evaluate_suggestion(
            answer,
            hedge_phrases=hedge_lines,
            policy_phrases=policy_lines,
        )
        logger.info(
            "grounded_rag_guardrail_result",
            extra={
                "trace_id": ctx.trace_id,
                "valid": decision.valid,
                "score": decision.score,
                "failure_reasons": list(decision.reasons),
            },
        )
        if not decision.valid:
            return self._skip(
                reason="guardrail_invalid",
                ctx=ctx,
                question=question,
                chunks=chunks,
                guardrail_score=decision.score,
                guardrail_failure_reasons=list(decision.reasons),
            )

        contains_profanity = get_russian_normalizer().contains_profanity(
            answer, custom_lemmas=profanity_lines
        )
        logger.info(
            "grounded_rag_profanity_result",
            extra={
                "trace_id": ctx.trace_id,
                "contains_profanity": contains_profanity,
            },
        )
        if contains_profanity:
            return self._skip(
                reason="profanity_detected",
                ctx=ctx,
                question=question,
                chunks=chunks,
            )

        text = answer.strip()
        logger.info(
            "grounded_rag_delivered",
            extra={
                "trace_id": ctx.trace_id,
                "text_length": len(text),
                "retrieval_source_ids": [c.source_id for c in chunks],
                "guardrail_score": decision.score,
            },
        )
        return AnswerResult(
            handled=True,
            text=text,
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
            "chunk_confidential_flags": [chunk.is_confidential for chunk in chunks],
            "chunk_project_ids": [chunk.project_id for chunk in chunks],
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
