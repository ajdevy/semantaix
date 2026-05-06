from __future__ import annotations

import httpx


class TelegramBotSender:
    def __init__(self, *, bot_token: str) -> None:
        self.bot_token = bot_token

    async def send_message(self, *, chat_id: int, text: str) -> int:
        if self.bot_token == "replace-me" or not self.bot_token:
            raise RuntimeError("missing_bot_token")

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            response.raise_for_status()
            payload = response.json()
            return int(payload["result"]["message_id"])
