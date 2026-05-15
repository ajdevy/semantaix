from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from services.bot_gateway.app.telegram_file_send import (
    TelegramFileSender,
    TelegramFileSendError,
)


def _factory(transport: httpx.MockTransport):
    def _build(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return _build


@pytest.mark.asyncio
async def test_send_document_by_file_id_happy_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 42}}
        )

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="TKN", http_client_factory=_factory(transport)
    )
    result = await sender.send_document_by_file_id(
        chat_id=12345, file_id="tg-file-id-1"
    )
    assert result["ok"] is True
    assert result["result"]["message_id"] == 42
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/sendDocument")
    assert "application/json" in captured["content_type"]
    body = captured["body"].decode("utf-8")
    assert "tg-file-id-1" in body
    assert "12345" in body


@pytest.mark.asyncio
async def test_send_document_by_file_id_accepts_caption() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="TKN", http_client_factory=_factory(transport)
    )
    await sender.send_document_by_file_id(
        chat_id="@bob", file_id="fid", caption="hello"
    )
    assert "hello" in captured["body"]
    assert "@bob" in captured["body"]


@pytest.mark.asyncio
async def test_send_document_by_file_id_telegram_error_raises_clean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="SECRET_TOKEN_123",
        http_client_factory=_factory(transport),
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_by_file_id(chat_id=10, file_id="fid")
    assert exc.value.reason == "telegram_send_failed"
    assert exc.value.description == "Bad Request: chat not found"
    assert "SECRET_TOKEN_123" not in str(exc.value)


@pytest.mark.asyncio
async def test_send_document_by_file_id_network_error_categorised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="SECRET_X",
        http_client_factory=_factory(transport),
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_by_file_id(chat_id=10, file_id="fid")
    assert exc.value.reason == "telegram_network_error"
    assert "SECRET_X" not in str(exc.value)


@pytest.mark.asyncio
async def test_send_document_local_uploads_multipart(tmp_path: Path) -> None:
    pdf = tmp_path / "demo.pdf"
    pdf.write_bytes(b"%PDF-DEMO-CONTENT")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="TKN", http_client_factory=_factory(transport)
    )
    result = await sender.send_document_local(
        chat_id=12345, path=pdf, file_name="demo.pdf", caption="cap"
    )
    assert result["ok"] is True
    assert captured["url"].endswith("/sendDocument")
    assert "multipart/form-data" in captured["content_type"]
    body = bytes(captured["body"])
    assert b"%PDF-DEMO-CONTENT" in body
    assert b"demo.pdf" in body
    assert b"cap" in body
    assert b"12345" in body


@pytest.mark.asyncio
async def test_send_document_local_telegram_error_raises_clean(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "description": "Forbidden: bot blocked"}
        )

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="ANOTHER_SECRET",
        http_client_factory=_factory(transport),
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_local(chat_id=1, path=pdf)
    assert exc.value.reason == "telegram_send_failed"
    assert exc.value.description == "Forbidden: bot blocked"
    assert "ANOTHER_SECRET" not in str(exc.value)


@pytest.mark.asyncio
async def test_send_document_local_missing_file_raises(tmp_path: Path) -> None:
    sender = TelegramFileSender(
        bot_token="TKN",
        http_client_factory=_factory(httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_local(
            chat_id=1, path=tmp_path / "missing.pdf"
        )
    assert exc.value.reason == "local_file_missing"


@pytest.mark.asyncio
async def test_send_document_by_file_id_non_json_response_categorised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>upstream</html>")

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="TKN", http_client_factory=_factory(transport)
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_by_file_id(chat_id=1, file_id="fid")
    assert exc.value.reason == "telegram_send_failed"


@pytest.mark.asyncio
async def test_send_document_uses_custom_base_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="TKN",
        http_client_factory=_factory(transport),
        base_url="http://local-bot-api:8081",
    )
    await sender.send_document_by_file_id(chat_id=1, file_id="fid")
    assert any("local-bot-api:8081" in u for u in seen)
    assert all("api.telegram.org" not in u for u in seen)


@pytest.mark.asyncio
async def test_send_document_local_network_error_categorised(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    transport = httpx.MockTransport(handler)
    sender = TelegramFileSender(
        bot_token="SECRET_MULTIPART",
        http_client_factory=_factory(transport),
    )
    with pytest.raises(TelegramFileSendError) as exc:
        await sender.send_document_local(chat_id=1, path=pdf)
    assert exc.value.reason == "telegram_network_error"
    assert "SECRET_MULTIPART" not in str(exc.value)
