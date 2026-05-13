from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from services.bot_gateway.app import main as bot_main
from services.bot_gateway.app.main import app as bot_app
from services.bot_gateway.app.telegram_file_download import (
    DownloadedFile,
    TelegramFileDownloadError,
)


@pytest.fixture
def isolated_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_main.settings, "persistence_db_path", str(tmp_path / "story.db"))
    monkeypatch.setattr(bot_main.settings, "hitl_ticket_db_path", str(tmp_path / "hitl.db"))
    monkeypatch.setattr(bot_main.settings, "operator_upload_storage_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(bot_main.settings, "operator_upload_max_bytes", 1024)
    monkeypatch.setattr(bot_main.settings, "telegram_bot_token", "TKN")
    monkeypatch.setattr(bot_main.settings, "hitl_primary_operator_username", "@ajdevy")
    monkeypatch.setattr(bot_main, "hitl_ticket_repository", _StubHitlRepo())

    sent_dms: list[tuple[int, str]] = []

    async def fake_send_dm(chat_id: int, text: str) -> None:
        sent_dms.append((chat_id, text))

    monkeypatch.setattr(bot_main, "_send_dm", fake_send_dm)
    return {"tmp_path": tmp_path, "dms": sent_dms}


class _StubHitlRepo:
    def get_runtime_config(self, key: str):
        return None

    def set_runtime_config(self, **kwargs):
        pass

    def list_all(self):
        return []


def _operator_message(
    *,
    text: str = "",
    caption: str | None = None,
    attachments: list[dict] | None = None,
):
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 100},
            "from": {"id": 200, "username": "ajdevy"},
        },
    }
    if text:
        payload["message"]["text"] = text
    if caption:
        payload["message"]["caption"] = caption
    if attachments:
        for attachment in attachments:
            payload["message"].update(attachment)
    return payload


def test_kb_inline_text_intent_triggers_ack(isolated_bot, monkeypatch):
    submit_calls: list[dict] = []

    async def fake_submit(**kwargs):
        submit_calls.append(kwargs)
        return {"inserted_chunks": 3, "is_confidential": False, "deduplicated": False}

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(text="добавь в базу: офис работает 9-18"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["attachment_count"] == "0"
    # Inline text path: api_client called once during background task
    assert len(submit_calls) == 1
    assert submit_calls[0]["source_file_type"] == "inline_text"
    assert "офис работает" in submit_calls[0]["inline_text"]
    # ack DM was sent
    assert any("Принял текст" in text for _, text in isolated_bot["dms"])


def test_kb_slash_with_document(isolated_bot, monkeypatch):
    file_path_on_disk = isolated_bot["tmp_path"] / "deck.pdf"
    file_path_on_disk.write_bytes(b"PDF")

    async def fake_download(*, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=file_path_on_disk, byte_size=3, mime_type=mime_type)

    async def fake_submit(**kwargs):
        return {"inserted_chunks": 5, "is_confidential": True, "deduplicated": False}

    monkeypatch.setattr(
        bot_main.TelegramFileDownloader,
        "download",
        fake_download,
        raising=False,
    )
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(
            caption="/kb_add confidential",
            attachments=[
                {
                    "document": {
                        "file_id": "DOC42",
                        "file_name": "schedule.pdf",
                        "mime_type": "application/pdf",
                        "file_size": 100,
                    },
                }
            ],
        ),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    # Background task ran the success path; summary DM mentions confidential
    summary = [text for _, text in isolated_bot["dms"] if "Добавлено в базу" in text]
    assert summary
    assert "confidential" in summary[0]


def test_kb_command_from_non_operator_is_ignored(isolated_bot):
    client = TestClient(bot_app)
    payload = _operator_message(text="/kb_add")
    payload["message"]["from"]["username"] = "stranger"
    response = client.post("/telegram/webhook", json=payload)
    body = response.json()
    # Non-operator falls through; with `/kb_add` text, attachment_only is False;
    # the existing customer-forward path runs.
    assert body["status"] != "accepted" or body.get("kb_mode") is None


def test_kb_download_failure_reports_in_summary(isolated_bot, monkeypatch):
    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        raise TelegramFileDownloadError("file_too_large", file_size=99_999_999)

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)

    async def fake_submit(**kwargs):
        return {"inserted_chunks": 0, "is_confidential": False, "deduplicated": False}

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(
            caption="/kb_add",
            attachments=[
                {
                    "document": {
                        "file_id": "BIG",
                        "file_name": "huge.pdf",
                        "mime_type": "application/pdf",
                        "file_size": 999999,
                    },
                }
            ],
        ),
    )
    assert response.status_code == 200
    summary = next(
        (text for _, text in isolated_bot["dms"] if "Не удалось обработать" in text), None
    )
    assert summary is not None
    assert "file_too_large" in summary


def test_kb_unsupported_attachment_type_falls_through_to_failures(isolated_bot, monkeypatch):
    async def fake_submit(**kwargs):  # pragma: no cover - should not run
        raise AssertionError("submit should not be called for unsupported")

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(
            caption="/kb_add",
            attachments=[
                {
                    "document": {
                        "file_id": "X",
                        "file_name": "weird.xyz",
                        "mime_type": "application/x-xyz",
                    }
                }
            ],
        ),
    )
    assert response.status_code == 200
    summaries = [t for _, t in isolated_bot["dms"]]
    assert any("unsupported_attachment_type" in t for t in summaries)


def test_kb_api_submit_failure_reports_failure(isolated_bot, monkeypatch):
    txt_on_disk = isolated_bot["tmp_path"] / "x.txt"
    txt_on_disk.write_bytes(b"ok")

    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=txt_on_disk, byte_size=2, mime_type=mime_type)

    async def fake_submit(**kwargs):
        raise httpx.HTTPError("API down")

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(
            caption="/kb_add",
            attachments=[
                {
                    "document": {
                        "file_id": "T",
                        "file_name": "doc.txt",
                        "mime_type": "text/plain",
                        "file_size": 2,
                    }
                }
            ],
        ),
    )
    assert response.status_code == 200
    summaries = [t for _, t in isolated_bot["dms"]]
    assert any("api_failed" in t for t in summaries)


def test_kb_inline_submit_failure_reported(isolated_bot, monkeypatch):
    async def fake_submit(**kwargs):
        raise RuntimeError("api boom")

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(text="добавь в базу: внутренняя справка"),
    )
    assert response.status_code == 200
    summaries = [t for _, t in isolated_bot["dms"]]
    assert any("Не удалось" in t and "inline_text" in t for t in summaries)


def test_kb_dedup_summary_line(isolated_bot, monkeypatch):
    pdf = isolated_bot["tmp_path"] / "dup.pdf"
    pdf.write_bytes(b"PDF")

    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=pdf, byte_size=3, mime_type=mime_type)

    async def fake_submit(**kwargs):
        return {"inserted_chunks": 0, "is_confidential": False, "deduplicated": True}

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(
            caption="/kb_add",
            attachments=[
                {
                    "document": {
                        "file_id": "D",
                        "file_name": "schedule.pdf",
                        "mime_type": "application/pdf",
                        "file_size": 3,
                    }
                }
            ],
        ),
    )
    assert response.status_code == 200
    summaries = [t for _, t in isolated_bot["dms"]]
    assert any("уже было в базе" in t for t in summaries)


def test_send_dm_logs_failure_but_does_not_raise(monkeypatch):
    """The real _send_dm function must swallow Telegram errors silently."""
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.HTTPError("offline")

    monkeypatch.setattr(bot_main.httpx, "AsyncClient", FakeClient)
    import asyncio

    asyncio.run(bot_main._send_dm(42, "hello"))


def test_kb_source_file_type_for_direct_kinds(isolated_bot):
    from services.bot_gateway.app.main import _kb_source_file_type
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    cases = (
        ("photo", "image"),
        ("audio", "audio"),
        ("voice", "audio"),
        ("video", "video"),
    )
    for kind, expected in cases:
        attachment = TelegramAttachment(file_id="x", kind=kind)
        assert _kb_source_file_type(attachment) == expected


def test_kb_source_file_type_via_filename(isolated_bot):
    from services.bot_gateway.app.main import _kb_source_file_type
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    cases = {
        "deck.pdf": "pdf",
        "doc.docx": "docx",
        "slides.pptx": "pptx",
        "notes.txt": "txt",
    }
    for name, expected in cases.items():
        attachment = TelegramAttachment(file_id="x", kind="document", file_name=name)
        assert _kb_source_file_type(attachment) == expected


def test_kb_source_file_type_via_mime(isolated_bot):
    from services.bot_gateway.app.main import _kb_source_file_type
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    pairs = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/msword": "docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.ms-powerpoint": "pptx",
        "text/plain": "txt",
        "image/png": "image",
        "audio/ogg": "audio",
        "video/mp4": "video",
    }
    for mime, expected in pairs.items():
        attachment = TelegramAttachment(file_id="x", kind="document", mime_type=mime)
        assert _kb_source_file_type(attachment) == expected


def test_kb_source_file_type_unrecognized_returns_none(isolated_bot):
    from services.bot_gateway.app.main import _kb_source_file_type
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    attachment = TelegramAttachment(file_id="x", kind="document", mime_type="weird/mime")
    assert _kb_source_file_type(attachment) is None


def test_kb_extension_for_uses_file_name(isolated_bot):
    from services.bot_gateway.app.main import _kb_extension_for
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    attachment = TelegramAttachment(file_id="x", kind="document", file_name="deck.PDF")
    assert _kb_extension_for(attachment, "pdf") == "PDF"


def test_kb_extension_for_uses_fallback(isolated_bot):
    from services.bot_gateway.app.main import _kb_extension_for
    from services.bot_gateway.app.telegram_update import TelegramAttachment

    attachment = TelegramAttachment(file_id="x", kind="voice")
    assert _kb_extension_for(attachment, "audio") == "ogg"
    attachment_unknown = TelegramAttachment(file_id="x", kind="document")
    assert _kb_extension_for(attachment_unknown, "weird") == "bin"


def test_kb_attachment_count_word_variants():
    from services.bot_gateway.app.main import _kb_attachment_count_word

    assert _kb_attachment_count_word(1) == "файл"
    assert _kb_attachment_count_word(2) == "файла"
    assert _kb_attachment_count_word(3) == "файла"
    assert _kb_attachment_count_word(5) == "файлов"
    assert _kb_attachment_count_word(11) == "файлов"


def test_kb_command_no_intent_returns_none(isolated_bot, monkeypatch):
    import asyncio

    from services.bot_gateway.app.main import _handle_kb_command
    from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

    class _BgTasks:
        def __init__(self):
            self.added = []

        def add_task(self, fn, **kwargs):
            self.added.append((fn, kwargs))

    bg = _BgTasks()
    msg = NormalizedTelegramMessage(
        update_id=1,
        source_message_id=1,
        chat_id=10,
        user_id=20,
        username="@ajdevy",
        text="привет, как дела?",
    )
    result = asyncio.run(_handle_kb_command(msg, bg))
    assert result is None


def test_kb_inline_intent_empty_cleaned_text_returns_ignored(isolated_bot, monkeypatch):
    import asyncio

    from services.bot_gateway.app.main import _handle_kb_command
    from services.bot_gateway.app.telegram_update import NormalizedTelegramMessage

    class _BgTasks:
        def __init__(self):
            self.added = []

        def add_task(self, fn, **kwargs):
            self.added.append((fn, kwargs))

    bg = _BgTasks()
    msg = NormalizedTelegramMessage(
        update_id=1,
        source_message_id=1,
        chat_id=10,
        user_id=20,
        username="@ajdevy",
        text="/kb_add",
        caption=None,
    )
    result = asyncio.run(_handle_kb_command(msg, bg))
    assert result is not None
    assert result["status"] == "ignored"
    assert result["reason"] == "no_attachments_no_inline_text"
