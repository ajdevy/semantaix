"""Download a Telegram file by `file_id` to a local storage directory.

The downloader performs the two-step Bot API dance: `getFile` returns a
relative `file_path`, then the actual binary lives at the file CDN. We
reject files larger than `max_bytes` before issuing the second request so
oversize uploads cost a single round-trip.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx


class TelegramFileDownloadError(Exception):
    def __init__(self, reason: str, *, file_size: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.file_size = file_size


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    byte_size: int
    mime_type: str | None


HttpClientFactory = Callable[..., httpx.AsyncClient]


class TelegramFileDownloader:
    def __init__(
        self,
        *,
        bot_token: str,
        storage_dir: str | Path,
        max_bytes: int,
        http_client_factory: HttpClientFactory = httpx.AsyncClient,
        timeout_seconds: int = 60,
    ) -> None:
        self._bot_token = bot_token
        self._storage_dir = Path(storage_dir)
        self._max_bytes = max_bytes
        self._client_factory = http_client_factory
        self._timeout = timeout_seconds

    async def download(
        self,
        *,
        file_id: str,
        suggested_extension: str,
        mime_type: str | None = None,
    ) -> DownloadedFile:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        get_file_url = f"https://api.telegram.org/bot{self._bot_token}/getFile"

        async with self._client_factory(timeout=self._timeout) as client:
            response = await client.get(get_file_url, params={"file_id": file_id})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("ok"):
                raise TelegramFileDownloadError("telegram_get_file_failed")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise TelegramFileDownloadError("telegram_get_file_missing_result")
            file_path = result.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                raise TelegramFileDownloadError("telegram_get_file_missing_path")
            reported_size = result.get("file_size")
            if isinstance(reported_size, int) and reported_size > self._max_bytes:
                raise TelegramFileDownloadError("file_too_large", file_size=reported_size)

            cdn_url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"
            extension = suggested_extension.lstrip(".") or "bin"
            destination = self._storage_dir / f"{uuid.uuid4().hex}.{extension}"
            written = 0
            async with client.stream("GET", cdn_url) as cdn_response:
                cdn_response.raise_for_status()
                with destination.open("wb") as fp:
                    async for chunk in cdn_response.aiter_bytes(chunk_size=65536):
                        fp.write(chunk)
                        written += len(chunk)
                        if written > self._max_bytes:
                            fp.close()
                            destination.unlink(missing_ok=True)
                            raise TelegramFileDownloadError(
                                "file_too_large", file_size=written
                            )
            return DownloadedFile(
                path=destination,
                byte_size=written,
                mime_type=mime_type,
            )
