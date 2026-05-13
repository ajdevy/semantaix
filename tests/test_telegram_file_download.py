from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from services.bot_gateway.app.telegram_file_download import (
    DownloadedFile,
    TelegramFileDownloader,
    TelegramFileDownloadError,
)


def _make_factory(transport: httpx.MockTransport):
    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_happy_path_downloads_to_storage_dir(tmp_path: Path):
    payload_body = b"PDF-BODY-BYTES"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_path": "documents/file_42.pdf",
                        "file_size": len(payload_body),
                    },
                },
            )
        if request.url.path.endswith("/documents/file_42.pdf"):
            return httpx.Response(200, content=payload_body)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )

    result = await downloader.download(
        file_id="abc", suggested_extension="pdf", mime_type="application/pdf"
    )
    assert isinstance(result, DownloadedFile)
    assert result.byte_size == len(payload_body)
    assert result.mime_type == "application/pdf"
    assert result.path.parent == tmp_path
    assert result.path.read_bytes() == payload_body


@pytest.mark.asyncio
async def test_oversize_reported_size_rejected_before_download(tmp_path: Path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"file_path": "documents/huge.bin", "file_size": 999_999_999},
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="bin")
    assert exc.value.reason == "file_too_large"
    assert exc.value.file_size == 999_999_999
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_missing_file_path_raises(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_missing_path"


@pytest.mark.asyncio
async def test_get_file_not_ok_raises(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False})

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_failed"


@pytest.mark.asyncio
async def test_get_file_non_dict_result_raises(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": ["not", "a", "dict"]})

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_missing_result"


@pytest.mark.asyncio
async def test_get_file_non_dict_payload_raises(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_failed"


@pytest.mark.asyncio
async def test_extension_fallback_to_bin(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "documents/a.bin", "file_size": 4}},
            )
        return httpx.Response(200, content=b"abcd")

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    result = await downloader.download(file_id="x", suggested_extension="")
    assert result.path.suffix == ".bin"


@pytest.mark.asyncio
async def test_streamed_oversize_aborts_and_cleans_up(tmp_path: Path):
    body = b"A" * 2048

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "documents/x.bin"}},
            )
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="bin")
    assert exc.value.reason == "file_too_large"
    leftover = list(tmp_path.iterdir())
    assert leftover == []
