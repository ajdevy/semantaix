from __future__ import annotations

from typing import Protocol

from services.api.app.answerers import AnswerContext, AnswerResult
from services.api.app.guardrails import evaluate_suggestion
from services.api.app.openrouter_client import OpenRouterClient
from services.api.app.rag import RagChunk
from services.api.app.russian_text import get_russian_normalizer

_SENTINEL = "ESCALATE_TO_HUMAN"


class _RagReader(Protocol):
    def retrieve(self, *, query: str, limit: int = 3) -> list[RagChunk]: ...


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
        chunks = self._rag.retrieve(query=question, limit=3)
        if not chunks or chunks[0].score < ctx.grounding_threshold:
            return AnswerResult(handled=False)

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
        except Exception:
            return AnswerResult(handled=False)

        if answer.strip().upper() == _SENTINEL:
            return AnswerResult(handled=False)

        try:
            verdict = await self._llm.verify_grounding(
                question=question,
                answer=answer,
                snippets=chunks,
            )
        except Exception:
            return AnswerResult(handled=False)
        if verdict.label != "GROUNDED":
            return AnswerResult(handled=False)

        decision = evaluate_suggestion(answer)
        if not decision.valid:
            return AnswerResult(handled=False)

        if get_russian_normalizer().contains_profanity(answer):
            return AnswerResult(handled=False)

        return AnswerResult(
            handled=True,
            text=answer.strip(),
            response_mode="grounded_rag",
            metadata={
                "retrieval": [
                    {
                        "source_id": chunk.source_id,
                        "chunk_text": chunk.chunk_text,
                        "score": chunk.score,
                    }
                    for chunk in chunks
                ],
                "verifier": verdict.reason,
                "guardrail_score": decision.score,
            },
        )
