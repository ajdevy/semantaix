from __future__ import annotations

import httpx


class TelegramBotSender:
    def __init__(self, *, bot_token: str) -> None:
        self.bot_token = bot_token

    def _require_token(self) -> None:
        if self.bot_token == "replace-me" or not self.bot_token:
            raise RuntimeError("missing_bot_token")

    def _is_token_configured(self) -> bool:
        return bool(self.bot_token) and self.bot_token != "replace-me"

    async def send_message(self, *, chat_id: int, text: str) -> int:
        self._require_token()

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            response.raise_for_status()
            payload = response.json()
            return int(payload["result"]["message_id"])

    async def _call_identity_method(
        self, *, method: str, json_body: dict[str, str]
    ) -> dict:
        """POST a setMyX identity update.

        Returns the parsed Bot API envelope (``{"ok": bool, ...}``). Does not
        raise on ``ok=false`` — callers (operator UI, startup sync) need to
        report Telegram's verdict back to the operator rather than crash.
        """
        if not self._is_token_configured():
            return {"ok": False, "skipped": "missing_bot_token"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/{method}",
                json=json_body,
            )
            response.raise_for_status()
            return response.json()

    async def set_my_name(self, *, name: str) -> dict:
        return await self._call_identity_method(
            method="setMyName", json_body={"name": name}
        )

    async def set_my_description(self, *, description: str) -> dict:
        return await self._call_identity_method(
            method="setMyDescription", json_body={"description": description}
        )

    async def set_my_short_description(self, *, short_description: str) -> dict:
        return await self._call_identity_method(
            method="setMyShortDescription",
            json_body={"short_description": short_description},
        )
