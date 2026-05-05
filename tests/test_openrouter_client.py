from unittest.mock import AsyncMock, Mock

import pytest

from services.api.app.openrouter_client import OpenRouterClient


@pytest.mark.asyncio
async def test_openrouter_client_requires_api_key():
    client = OpenRouterClient()
    client.api_key = None
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await client.suggest("hello")


@pytest.mark.asyncio
async def test_openrouter_client_sends_context_and_parses_response(monkeypatch):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "final answer"}}]}
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

    client = OpenRouterClient()
    client.api_key = "token"
    client.base_url = "https://openrouter.ai/api/v1"
    client.model = "anthropic/claude-sonnet-4"

    result = await client.suggest(
        "What should I reply?",
        context=[{"role": "assistant", "content": "prior context"}],
    )

    assert result == "final answer"
    post_args = http_client.post.call_args.kwargs
    assert post_args["headers"]["Authorization"] == "Bearer token"
    assert post_args["json"]["model"] == "anthropic/claude-sonnet-4"
    assert post_args["json"]["messages"][-1] == {"role": "user", "content": "What should I reply?"}
    assert post_args["json"]["messages"][1] == {"role": "assistant", "content": "prior context"}
