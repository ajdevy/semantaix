from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from platform_common.settings import get_settings
from services.api.app.rag import RagChunk

_GROUNDING_SYSTEM_PROMPT_TEMPLATE = (
    "Ты — {first_name} {last_name}, сотрудник поддержки, отвечающий клиенту "
    "от первого лица. Никогда не пиши, что ты бот, ассистент, AI, ИИ, "
    "нейросеть или языковая модель. Если спросят, отвечай как обычный "
    "человек на работе. Отвечай на том же языке, что и вопрос пользователя; "
    "если вопрос на русском — отвечай формальным русским, без сленга и "
    "нецензурной лексики. Используй ТОЛЬКО приведённые ниже фрагменты "
    "знаний и сегодняшнюю дату ({today_iso}). Если ответ не полностью "
    "подтверждён этими источниками — верни строго `ESCALATE_TO_HUMAN` без "
    "каких-либо других слов. Не хеджируй (\"возможно\", \"вроде\", "
    "\"наверное\", \"хз\") — либо точный ответ, либо `ESCALATE_TO_HUMAN`."
)

_VERIFIER_SYSTEM_PROMPT = (
    "Given the question, the candidate answer, and the snippets, decide "
    "whether the answer is fully supported by the snippets (and today's "
    "date). Reply with exactly `GROUNDED: <one-sentence reason>` or "
    "`NOT_GROUNDED: <one-sentence reason>`. The candidate answer may be "
    "in Russian or English; the verdict reason should be in English for "
    "log readability."
)


@dataclass(frozen=True)
class GroundingVerdict:
    label: str  # "GROUNDED" or "NOT_GROUNDED"
    reason: str


def _format_snippets(snippets: list[RagChunk]) -> str:
    return "\n".join(f"- [{chunk.source_id}] {chunk.chunk_text}" for chunk in snippets)


def _build_grounding_system_prompt(
    *, first_name: str, last_name: str, today_iso: str
) -> str:
    return _GROUNDING_SYSTEM_PROMPT_TEMPLATE.format(
        first_name=first_name, last_name=last_name, today_iso=today_iso
    )


class OpenRouterClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.grounding_model = settings.openrouter_grounding_model

    async def _chat(
        self, *, model: str, messages: list[dict[str, Any]]
    ) -> str:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        payload = {"model": model, "messages": messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def answer_grounded(
        self,
        *,
        question: str,
        snippets: list[RagChunk],
        today_iso: str,
        persona_first_name: str,
        persona_last_name: str,
        model: str | None = None,
    ) -> str:
        system = _build_grounding_system_prompt(
            first_name=persona_first_name,
            last_name=persona_last_name,
            today_iso=today_iso,
        )
        user_block = (
            "Snippets:\n"
            + _format_snippets(snippets)
            + "\n\nQuestion:\n"
            + question
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_block},
        ]
        return await self._chat(
            model=model or self.grounding_model, messages=messages
        )

    async def verify_grounding(
        self,
        *,
        question: str,
        answer: str,
        snippets: list[RagChunk],
        model: str | None = None,
    ) -> GroundingVerdict:
        user_block = (
            "Snippets:\n"
            + _format_snippets(snippets)
            + "\n\nQuestion:\n"
            + question
            + "\n\nCandidate answer:\n"
            + answer
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _VERIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_block},
        ]
        raw = await self._chat(
            model=model or self.grounding_model, messages=messages
        )
        return _parse_verdict(raw)


def _parse_verdict(raw: str) -> GroundingVerdict:
    text = raw.strip()
    upper = text.upper()
    if upper.startswith("GROUNDED"):
        reason = text.split(":", 1)[1].strip() if ":" in text else ""
        return GroundingVerdict(label="GROUNDED", reason=reason)
    if upper.startswith("NOT_GROUNDED"):
        reason = text.split(":", 1)[1].strip() if ":" in text else ""
        return GroundingVerdict(label="NOT_GROUNDED", reason=reason)
    # Defensive: treat unparseable verifier output as NOT_GROUNDED so we
    # escalate to HITL rather than risk a hallucinated answer.
    return GroundingVerdict(
        label="NOT_GROUNDED", reason=f"unparseable verifier output: {text[:60]}"
    )
