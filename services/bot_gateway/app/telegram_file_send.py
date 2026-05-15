"""Send a document to a Telegram chat.

Two transports:
  * `send_document_by_file_id` posts JSON with the saved `file_id` — Telegram
    serves the original bytes from its CDN without a re-upload. Critically,
    this path has NO 20 MB cap (unlike `getFile`), so files that were too big
    to download into the bot can still be resent to customers.
  * `send_document_local` falls back to a multipart upload when the saved
    `file_id` is stale or the operator wants to send a locally stored file.

All Telegram errors are caught and re-raised as `TelegramFileSendError` with
a categorised `reason` and (when available) a `description`. Raw httpx errors
never escape, so the bot token in the request URL is never surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx


class TelegramFileSendError(Exception):
    def __init__(
        self, reason: str, *, description: str | None = None
    ) -> None:
        super().__init__(
            reason if description is None else f"{reason}:{description}"
        )
        self.reason = reason
        self.description = description


@dataclass(frozen=True)
class _Endpoint:
    bot_token: str

    @property
    def url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/sendDocument"


HttpClientFactory = Callable[..., httpx.AsyncClient]


class TelegramFileSender:
    def __init__(
        self,
        *,
        bot_token: str,
        http_client_factory: HttpClientFactory = httpx.AsyncClient,
        timeout_seconds: int = 60,
    ) -> None:
        self._endpoint = _Endpoint(bot_token=bot_token)
        self._client_factory = http_client_factory
        self._timeout = timeout_seconds

    async def send_document_by_file_id(
        self,
        *,
        chat_id: int | str,
        file_id: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "document": file_id}
        if caption is not None:
            body["caption"] = caption
        return await self._post_json(body)

    async def send_document_local(
        self,
        *,
        chat_id: int | str,
        path: Path,
        file_name: str | None = None,
        caption: str | None = None,
    ) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise TelegramFileSendError("local_file_missing")
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        with path.open("rb") as fp:
            files = {"document": (file_name or path.name, fp.read())}
        return await self._post_multipart(data=data, files=files)

    async def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client_factory(timeout=self._timeout) as client:
                response = await client.post(self._endpoint.url, json=body)
        except httpx.HTTPError:
            raise TelegramFileSendError("telegram_network_error") from None
        return self._interpret(response)

    async def _post_multipart(
        self, *, data: dict[str, Any], files: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            async with self._client_factory(timeout=self._timeout) as client:
                response = await client.post(
                    self._endpoint.url, data=data, files=files
                )
        except httpx.HTTPError:
            raise TelegramFileSendError("telegram_network_error") from None
        return self._interpret(response)

    def _interpret(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise TelegramFileSendError("telegram_send_failed")
        if response.status_code >= 400 or not payload.get("ok"):
            description = payload.get("description")
            raise TelegramFileSendError(
                "telegram_send_failed",
                description=(
                    str(description) if description is not None else None
                ),
            )
        return payload
