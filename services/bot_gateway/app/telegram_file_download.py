"""Download a Telegram file by `file_id` to a local storage directory.

Cloud Bot API mode (default): two-step dance — `getFile` returns a relative
`file_path`, then the binary lives at the file CDN. Telegram caps `getFile`
at 20 MB.

Local Bot API server mode (`local_mode=True`): when the bot is pointed at a
self-hosted [tdlib/telegram-bot-api](https://github.com/tdlib/telegram-bot-api)
instance, `getFile` returns an absolute filesystem path. The bot reads the
file directly off the shared volume — no second HTTP call, and the size cap
rises from 20 MB to 2 GB.

All Telegram and network failures are caught and re-raised as
`TelegramFileDownloadError` with a categorised `reason`. Raw httpx errors
never escape: their `__str__` includes the request URL, which contains the
bot token. Categorised reasons supported:

  * `file_too_large` — `getFile` reported size > max_bytes, the CDN stream
    exceeded max_bytes, or Telegram returned 400 "Bad Request: file is too
    big" (its hard 20 MB cap for bot getFile, cloud mode only).
  * `telegram_get_file_failed` — any other getFile-level failure (kept as a
    coarse bucket for backward compatibility; the optional `description`
    attribute carries the Telegram-supplied detail).
  * `telegram_get_file_missing_path` / `telegram_get_file_missing_result`
    — payload shape we cannot recover from.
  * `telegram_network_error` — httpx-level failure reaching the Bot API.
  * `telegram_cdn_error` — file CDN returned a non-2xx response.
  * `local_file_missing` — local mode only: the path returned by `getFile`
    does not exist on disk (shared volume mis-mounted, file already pruned).
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx


class TelegramFileDownloadError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        file_size: int | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__(
            reason if description is None else f"{reason}:{description}"
        )
        self.reason = reason
        self.file_size = file_size
        self.description = description


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
        base_url: str = "https://api.telegram.org",
        local_mode: bool = False,
    ) -> None:
        self._bot_token = bot_token
        self._storage_dir = Path(storage_dir)
        self._max_bytes = max_bytes
        self._client_factory = http_client_factory
        self._timeout = timeout_seconds
        self._base_url = base_url.rstrip("/")
        self._local_mode = local_mode

    async def download(
        self,
        *,
        file_id: str,
        suggested_extension: str,
        mime_type: str | None = None,
    ) -> DownloadedFile:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        get_file_url = f"{self._base_url}/bot{self._bot_token}/getFile"

        async with self._client_factory(timeout=self._timeout) as client:
            try:
                response = await client.get(
                    get_file_url, params={"file_id": file_id}
                )
            except httpx.HTTPError:
                raise TelegramFileDownloadError(
                    "telegram_network_error"
                ) from None
            payload = self._validate_get_file_response(response)
            result = payload.get("result")
            if not isinstance(result, dict):
                raise TelegramFileDownloadError(
                    "telegram_get_file_missing_result"
                )
            file_path = result.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                raise TelegramFileDownloadError(
                    "telegram_get_file_missing_path"
                )
            reported_size = result.get("file_size")
            if isinstance(reported_size, int) and reported_size > self._max_bytes:
                raise TelegramFileDownloadError(
                    "file_too_large", file_size=reported_size
                )

            extension = suggested_extension.lstrip(".") or "bin"
            destination = self._storage_dir / f"{uuid.uuid4().hex}.{extension}"
            if self._local_mode:
                return self._copy_from_local_path(
                    source_path=Path(file_path),
                    destination=destination,
                    mime_type=mime_type,
                )
            return await self._fetch_from_cdn(
                client=client,
                file_path=file_path,
                destination=destination,
                mime_type=mime_type,
            )

    def _copy_from_local_path(
        self,
        *,
        source_path: Path,
        destination: Path,
        mime_type: str | None,
    ) -> DownloadedFile:
        """Local Bot API server returns a filesystem path — copy directly."""
        if not source_path.exists() or not source_path.is_file():
            raise TelegramFileDownloadError("local_file_missing")
        size = source_path.stat().st_size
        if size > self._max_bytes:
            raise TelegramFileDownloadError("file_too_large", file_size=size)
        try:
            shutil.copyfile(source_path, destination)
        except OSError:
            destination.unlink(missing_ok=True)
            raise TelegramFileDownloadError("local_file_missing") from None
        return DownloadedFile(
            path=destination, byte_size=size, mime_type=mime_type
        )

    async def _fetch_from_cdn(
        self,
        *,
        client: httpx.AsyncClient,
        file_path: str,
        destination: Path,
        mime_type: str | None,
    ) -> DownloadedFile:
        cdn_url = (
            f"{self._base_url}/file/bot{self._bot_token}/{file_path}"
        )
        written = 0
        try:
            async with client.stream("GET", cdn_url) as cdn_response:
                if cdn_response.status_code >= 400:
                    raise TelegramFileDownloadError("telegram_cdn_error")
                with destination.open("wb") as fp:
                    async for chunk in cdn_response.aiter_bytes(
                        chunk_size=65536
                    ):
                        fp.write(chunk)
                        written += len(chunk)
                        if written > self._max_bytes:
                            fp.close()
                            destination.unlink(missing_ok=True)
                            raise TelegramFileDownloadError(
                                "file_too_large", file_size=written
                            )
        except TelegramFileDownloadError:
            destination.unlink(missing_ok=True)
            raise
        except httpx.HTTPError:
            destination.unlink(missing_ok=True)
            raise TelegramFileDownloadError(
                "telegram_cdn_error"
            ) from None
        return DownloadedFile(
            path=destination, byte_size=written, mime_type=mime_type
        )

    @staticmethod
    def _validate_get_file_response(response: httpx.Response) -> dict:
        """Translate a getFile response into a parsed dict or categorised error.

        Returns the parsed JSON dict (ok=True) on success. Raises
        `TelegramFileDownloadError` on any error — we never let httpx's own
        `raise_for_status` run, because its message includes the request URL
        and that carries the bot token.
        """
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if (
            response.status_code < 400
            and isinstance(payload, dict)
            and payload.get("ok")
        ):
            return payload
        description = None
        if isinstance(payload, dict):
            raw_description = payload.get("description")
            if isinstance(raw_description, str) and raw_description:
                description = raw_description
        if description and "too big" in description.lower():
            raise TelegramFileDownloadError("file_too_large")
        if description is None:
            raise TelegramFileDownloadError("telegram_get_file_failed")
        raise TelegramFileDownloadError(
            "telegram_get_file_failed", description=description
        )
