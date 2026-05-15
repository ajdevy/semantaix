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


@pytest.mark.asyncio
async def test_get_file_returns_400_with_too_big_description(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: file is too big",
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="SUPER_SECRET_BOT_TOKEN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "file_too_large"
    assert "SUPER_SECRET_BOT_TOKEN" not in str(exc.value)


@pytest.mark.asyncio
async def test_get_file_returns_400_with_other_description(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: wrong file_id specified",
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="SEKRET",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_failed"
    assert exc.value.description == "Bad Request: wrong file_id specified"
    assert "SEKRET" not in str(exc.value)


@pytest.mark.asyncio
async def test_get_file_network_error_categorised(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="NOPE_NOPE",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_network_error"
    assert "NOPE_NOPE" not in str(exc.value)


@pytest.mark.asyncio
async def test_cdn_error_categorised_without_token_leak(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "documents/x.pdf"}},
            )
        return httpx.Response(503, text="cdn down")

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="LEAK_ME_NOT",
        storage_dir=tmp_path,
        max_bytes=10_000,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_cdn_error"
    assert "LEAK_ME_NOT" not in str(exc.value)


@pytest.mark.asyncio
async def test_no_bot_token_in_any_error_message(tmp_path: Path):
    bot_token = "123456789:ABCDEFGHabcdefghIJKLMNOPijklmnop"

    def make_downloader(transport: httpx.MockTransport) -> TelegramFileDownloader:
        return TelegramFileDownloader(
            bot_token=bot_token,
            storage_dir=tmp_path,
            max_bytes=1024,
            http_client_factory=_make_factory(transport),
        )

    scenarios = [
        # 400 too big
        lambda r: httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: file is too big"},
        ),
        # 400 other
        lambda r: httpx.Response(
            400, json={"ok": False, "description": "Bad Request: x"}
        ),
        # network
        _raise_connect_error,
        # cdn error
        _two_step_with_cdn_500,
    ]
    for handler in scenarios:
        transport = httpx.MockTransport(handler)
        downloader = make_downloader(transport)
        with pytest.raises(TelegramFileDownloadError) as exc:
            await downloader.download(file_id="x", suggested_extension="pdf")
        assert bot_token not in str(exc.value)
        assert "AB" + "CDEFGH" not in str(exc.value)


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("dns")


def _two_step_with_cdn_500(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getFile"):
        return httpx.Response(
            200, json={"ok": True, "result": {"file_path": "documents/x.pdf"}}
        )
    return httpx.Response(500, text="boom")


@pytest.mark.asyncio
async def test_get_file_html_response_categorised(tmp_path: Path):
    """A non-JSON response (e.g. an HTML 502 page from a proxy) classifies as
    telegram_get_file_failed without leaking the bot token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>upstream</html>")

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="SECRET_HTML",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_get_file_failed"
    assert "SECRET_HTML" not in str(exc.value)


@pytest.mark.asyncio
async def test_base_url_threaded_into_request(tmp_path: Path):
    """Custom base_url is used for both legs of the dance."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"file_path": "documents/x.pdf", "file_size": 4},
                },
            )
        return httpx.Response(200, content=b"DATA")

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path,
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
        base_url="http://local-bot-api:8081",
    )
    result = await downloader.download(file_id="x", suggested_extension="pdf")
    assert result.byte_size == 4
    assert any("local-bot-api:8081" in u for u in seen)
    assert all("api.telegram.org" not in u for u in seen)


@pytest.mark.asyncio
async def test_local_mode_reads_file_from_disk(tmp_path: Path):
    """When the local Bot API server is in use, file_path is an absolute path
    on a shared volume — we copy that file instead of making a CDN request."""
    storage_root = tmp_path / "telegram-bot-api"
    source = storage_root / "123/documents/file_42.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-150MB-EXAMPLE")
    cdn_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cdn_calls.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_path": str(source),
                        "file_size": source.stat().st_size,
                    },
                },
            )
        return httpx.Response(500, text="cdn must not be called in local mode")

    transport = httpx.MockTransport(handler)
    target_dir = tmp_path / "uploads"
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=target_dir,
        max_bytes=200 * 1024 * 1024,
        http_client_factory=_make_factory(transport),
        base_url="http://local-bot-api:8081",
        local_mode=True,
    )
    result = await downloader.download(
        file_id="x", suggested_extension="pdf", mime_type="application/pdf"
    )
    assert result.byte_size == source.stat().st_size
    assert result.path.parent == target_dir
    assert result.path.read_bytes() == source.read_bytes()
    # CDN was not called — only getFile
    assert all(p.endswith("/getFile") for p in cdn_calls)


@pytest.mark.asyncio
async def test_local_mode_oversize_reported_size_rejected(tmp_path: Path):
    storage_root = tmp_path / "telegram-bot-api"
    source = storage_root / "documents/big.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x" * 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"file_path": str(source), "file_size": 10_000},
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path / "uploads",
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
        local_mode=True,
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="bin")
    assert exc.value.reason == "file_too_large"


@pytest.mark.asyncio
async def test_local_mode_oversize_actual_file_rejected(tmp_path: Path):
    """getFile's reported size is within max_bytes, but the actual on-disk
    file is larger — we still reject before exposing it."""
    storage_root = tmp_path / "telegram-bot-api"
    source = storage_root / "documents/sneaky.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x" * 4096)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"file_path": str(source), "file_size": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path / "uploads",
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
        local_mode=True,
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="bin")
    assert exc.value.reason == "file_too_large"
    assert exc.value.file_size == 4096


@pytest.mark.asyncio
async def test_local_mode_copy_oserror_categorised(tmp_path, monkeypatch):
    """If shutil.copyfile fails mid-copy (volume unmounted, permission
    revoked), surface a categorised error rather than a raw OSError."""
    storage_root = tmp_path / "telegram-bot-api"
    source = storage_root / "documents/x.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"file_path": str(source), "file_size": 3},
            },
        )

    import shutil

    def boom(*args, **kwargs):
        raise OSError("simulated I/O error")

    monkeypatch.setattr(shutil, "copyfile", boom)
    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path / "uploads",
        max_bytes=1024,
        http_client_factory=_make_factory(transport),
        local_mode=True,
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "local_file_missing"


@pytest.mark.asyncio
async def test_local_mode_missing_file_raises(tmp_path: Path):
    """If the local Bot API server says the file exists but we cannot find
    it on the shared volume, surface a categorised error rather than a raw
    OSError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "file_path": "/nonexistent/path/x.pdf",
                    "file_size": 10,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    downloader = TelegramFileDownloader(
        bot_token="TKN",
        storage_dir=tmp_path / "uploads",
        max_bytes=10_000,
        http_client_factory=_make_factory(transport),
        local_mode=True,
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "local_file_missing"


@pytest.mark.asyncio
async def test_cdn_stream_httpx_error_categorised(tmp_path: Path):
    """A network-level error while streaming the CDN body classifies as
    telegram_cdn_error and cleans up the partial file."""

    class _BrokenAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise httpx.ReadError("stream broken")

    class _StreamCtx:
        def __init__(self, status_code: int):
            self.status_code = status_code

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def aiter_bytes(self, chunk_size: int = 0):
            return _BrokenAsyncIter()

    class _Client:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "documents/x.pdf"}},
            )

        def stream(self, method, url):
            return _StreamCtx(200)

    downloader = TelegramFileDownloader(
        bot_token="SECRET_STREAM",
        storage_dir=tmp_path,
        max_bytes=10_000,
        http_client_factory=lambda **kw: _Client(**kw),
    )
    with pytest.raises(TelegramFileDownloadError) as exc:
        await downloader.download(file_id="x", suggested_extension="pdf")
    assert exc.value.reason == "telegram_cdn_error"
    assert "SECRET_STREAM" not in str(exc.value)
    # Partial file must have been cleaned up
    assert list(tmp_path.iterdir()) == []
