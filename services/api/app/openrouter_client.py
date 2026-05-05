from __future__ import annotations

from typing import Any

import httpx

from platform_common.settings import get_settings


class OpenRouterClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.model = settings.openrouter_model

    async def suggest(self, user_text: str, context: list[dict[str, str]] | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are a helpful support assistant."},
        ]
        if context:
            messages.extend(context)
        messages.append({"role": "user", "content": user_text})

        payload = {"model": self.model, "messages": messages}
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
