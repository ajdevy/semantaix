from __future__ import annotations

import httpx


class TelegramBotSender:
    def __init__(
        self,
        *,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = bot_token
        self._base_url = base_url.rstrip("/")

    def _require_token(self) -> None:
        if self.bot_token == "replace-me" or not self.bot_token:
            raise RuntimeError("missing_bot_token")

    def _is_token_configured(self) -> bool:
        return bool(self.bot_token) and self.bot_token != "replace-me"

    async def send_message(self, *, chat_id: int, text: str) -> int:
        self._require_token()

        # One retry on transient failure (5xx or network error). 4xx is
        # client error (bad chat_id, blocked bot, message too long) — those
        # never become valid on retry, so we surface them immediately.
        # Idempotency note: Telegram does not dedupe sendMessage calls by
        # content, so a successful-but-disconnected first attempt would
        # double-post. We rely on api-side idempotency
        # (answer_traces.trace_id) to make that case rare; this retry is
        # specifically for fully-failed first attempts.
        url = f"{self._base_url}/bot{self.bot_token}/sendMessage"
        body = {"chat_id": chat_id, "text": text}
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status >= 500:
                    response = await client.post(url, json=body)
                    response.raise_for_status()
                else:
                    raise
            except httpx.TransportError:
                response = await client.post(url, json=body)
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
                f"{self._base_url}/bot{self.bot_token}/{method}",
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
