from unittest.mock import AsyncMock, Mock

import pytest

from services.api.app.openrouter_client import (
    GroundingVerdict,
    OpenRouterClient,
    _parse_verdict,
)
from services.api.app.rag import RagChunk


def _http_mock(monkeypatch, *, content: str):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    response.raise_for_status = Mock()

    http_client = AsyncMock()
    http_client.post.return_value = response

    async_client_cm = AsyncMock()
    async_client_cm.__aenter__.return_value = http_client
    async_client_cm.__aexit__.return_value = None

    monkeypatch.setattr(
        "services.api.app.openrouter_client.httpx.AsyncClient",
        lambda timeout: async_client_cm,
    )
    return http_client


def _snippet() -> RagChunk:
    return RagChunk(id=1, source_id="kb-1", chunk_text="text", score=0.9)


@pytest.mark.asyncio
async def test_answer_grounded_requires_api_key():
    client = OpenRouterClient()
    client.api_key = None
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await client.answer_grounded(
            question="hi",
            snippets=[_snippet()],
            today_iso="2026-05-11",
            persona_first_name="Анна",
            persona_last_name="Иванова",
        )


@pytest.mark.asyncio
async def test_answer_grounded_uses_grounding_model_and_sends_context(monkeypatch):
    http_client = _http_mock(monkeypatch, content="Final answer.")
    client = OpenRouterClient()
    client.api_key = "token"
    client.base_url = "https://openrouter.ai/api/v1"
    client.grounding_model = "google/gemini-2.0-flash-lite-001"

    result = await client.answer_grounded(
        question="Когда мой возврат?",
        snippets=[_snippet()],
        today_iso="2026-05-11",
        persona_first_name="Анна",
        persona_last_name="Иванова",
    )

    assert result == "Final answer."
    sent = http_client.post.call_args.kwargs["json"]
    assert sent["model"] == "google/gemini-2.0-flash-lite-001"
    assert sent["messages"][0]["role"] == "system"
    system_prompt = sent["messages"][0]["content"]
    assert "ESCALATE_TO_HUMAN" in system_prompt
    assert "2026-05-11" in system_prompt
    assert "Анна Иванова" in system_prompt
    # Persona must NOT identify the agent as a bot/assistant/AI.
    assert "Ты — ассистент" not in system_prompt
    assert "Когда мой возврат?" in sent["messages"][1]["content"]


@pytest.mark.asyncio
async def test_answer_grounded_respects_model_override(monkeypatch):
    http_client = _http_mock(monkeypatch, content="x")
    client = OpenRouterClient()
    client.api_key = "token"
    client.grounding_model = "default-model"

    await client.answer_grounded(
        question="q",
        snippets=[_snippet()],
        today_iso="2026-05-11",
        persona_first_name="Мария",
        persona_last_name="Петрова",
        model="override-model",
    )
    assert http_client.post.call_args.kwargs["json"]["model"] == "override-model"


@pytest.mark.asyncio
async def test_answer_grounded_injects_persona_name_into_system_prompt(monkeypatch):
    http_client = _http_mock(monkeypatch, content="ok")
    client = OpenRouterClient()
    client.api_key = "token"
    await client.answer_grounded(
        question="q",
        snippets=[_snippet()],
        today_iso="2026-05-11",
        persona_first_name="Иван",
        persona_last_name="Сидоров",
    )
    system_prompt = http_client.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert "Иван Сидоров" in system_prompt
    # The prompt must instruct the LLM not to self-identify as a bot.
    assert "не пиши, что ты бот" in system_prompt.lower()
    # And to speak as a first-person-plural employee of the company in the
    # snippets, never naming it in third person — locks in the voice rule
    # that prevents answers like "Компания X предлагает туры на багги".
    assert "от первого лица" in system_prompt.lower()
    assert "мы предлагаем" in system_prompt.lower()
    assert "запрещено" in system_prompt.lower()


@pytest.mark.asyncio
async def test_answer_grounded_strips_persona_when_last_name_is_empty(monkeypatch):
    """Last name is optional — the system prompt must render `Ты — Анна,` (no
    trailing space before the comma) so it reads naturally to the LLM."""
    http_client = _http_mock(monkeypatch, content="ok")
    client = OpenRouterClient()
    client.api_key = "token"
    await client.answer_grounded(
        question="q",
        snippets=[_snippet()],
        today_iso="2026-05-11",
        persona_first_name="Анна",
        persona_last_name="",
    )
    system_prompt = http_client.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert "Ты — Анна, сотрудник" in system_prompt
    assert "Ты — Анна , сотрудник" not in system_prompt
    assert "Ты —  Анна" not in system_prompt


@pytest.mark.asyncio
async def test_verify_grounding_parses_grounded(monkeypatch):
    _http_mock(monkeypatch, content="GROUNDED: matches the snippet exactly.")
    client = OpenRouterClient()
    client.api_key = "token"
    verdict = await client.verify_grounding(
        question="q", answer="a", snippets=[_snippet()]
    )
    assert verdict.label == "GROUNDED"
    assert "matches" in verdict.reason


@pytest.mark.asyncio
async def test_verify_grounding_parses_not_grounded(monkeypatch):
    _http_mock(monkeypatch, content="NOT_GROUNDED: snippet does not cover that.")
    client = OpenRouterClient()
    client.api_key = "token"
    verdict = await client.verify_grounding(
        question="q", answer="a", snippets=[_snippet()]
    )
    assert verdict.label == "NOT_GROUNDED"
    assert "snippet" in verdict.reason


def test_parse_verdict_unparseable_defaults_to_not_grounded():
    verdict = _parse_verdict("model emitted prose, no verdict prefix")
    assert verdict.label == "NOT_GROUNDED"
    assert "unparseable" in verdict.reason


def test_grounding_verdict_dataclass_immutable():
    v = GroundingVerdict(label="GROUNDED", reason="ok")
    with pytest.raises(Exception):
        v.label = "NOT_GROUNDED"  # type: ignore[misc]
