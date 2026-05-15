from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from services.bot_gateway.app import kb_session as kb_session_module
from services.bot_gateway.app import main as bot_main
from services.bot_gateway.app.kb_session import OperatorKbSessionRepository
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
    fresh_kb_repo = OperatorKbSessionRepository(str(tmp_path / "hitl.db"))
    monkeypatch.setattr(bot_main, "kb_session_repository", fresh_kb_repo)

    sent_dms: list[tuple[int, str]] = []

    async def fake_send_dm(chat_id: int, text: str) -> None:
        sent_dms.append((chat_id, text))

    monkeypatch.setattr(bot_main, "_send_dm", fake_send_dm)
    return {
        "tmp_path": tmp_path,
        "dms": sent_dms,
        "kb_session_repo": fresh_kb_repo,
    }


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


def test_kb_inline_intent_empty_cleaned_text_opens_session(isolated_bot, monkeypatch):
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
    assert result["status"] == "accepted"
    assert result["kb_mode"] == "session_opened"
    # No inline submit scheduled.
    assert bg.added == []
    # Session is now active in the repo.
    repo = isolated_bot["kb_session_repo"]
    assert repo.get_active(chat_id=10, username="@ajdevy") is not None
    # Operator was told what to do next.
    assert any("Жду файлы" in text for _, text in isolated_bot["dms"])


def test_kb_lemma_intent_without_files_opens_session_no_inline_submit(
    isolated_bot, monkeypatch
):
    """The operator's meta-request ('хочу добавить материалы…') must NOT be
    ingested as inline_text — it's a session-open signal, not knowledge."""
    submit_calls: list[dict] = []

    async def fake_submit(**kwargs):  # pragma: no cover - must not be called
        submit_calls.append(kwargs)
        return {"inserted_chunks": 0, "is_confidential": False, "deduplicated": False}

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)
    response = client.post(
        "/telegram/webhook",
        json=_operator_message(text="хочу добавить материалы в knowledge base"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["kb_mode"] == "session_opened"
    # No inline_text submitted (the meta-phrase would be garbage in RAG).
    assert submit_calls == []
    # Session is open for this operator/chat.
    repo = isolated_bot["kb_session_repo"]
    session = repo.get_active(chat_id=100, username="@ajdevy")
    assert session is not None
    assert session.is_confidential is False
    # Operator was prompted to send files.
    assert any("Жду файлы" in text for _, text in isolated_bot["dms"])


def test_kb_session_continuation_routes_pdf_after_text_intent(
    isolated_bot, monkeypatch
):
    """The headline scenario: operator types intent, then sends a PDF as a
    separate message. The PDF must be picked up via session continuation."""
    pdf = isolated_bot["tmp_path"] / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    submit_calls: list[dict] = []

    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=pdf, byte_size=8, mime_type=mime_type)

    async def fake_submit(**kwargs):
        submit_calls.append(kwargs)
        return {"inserted_chunks": 7, "is_confidential": False, "deduplicated": False}

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)

    # 1) Text-only message that opens the session.
    text_msg = _operator_message(text="хочу добавить материалы в knowledge base")
    text_msg["update_id"] = 1001
    text_msg["message"]["message_id"] = 1001
    resp_text = client.post("/telegram/webhook", json=text_msg)
    assert resp_text.json()["kb_mode"] == "session_opened"

    # 2) PDF-only message (no text, no caption) in the same chat.
    pdf_msg = _operator_message(
        attachments=[
            {
                "document": {
                    "file_id": "PDF1",
                    "file_name": "manual.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 8,
                }
            }
        ],
    )
    pdf_msg["update_id"] = 1002
    pdf_msg["message"]["message_id"] = 1002
    resp_pdf = client.post("/telegram/webhook", json=pdf_msg)
    body = resp_pdf.json()
    assert body["status"] == "accepted"
    assert body["kb_mode"] == "session_continuation"
    assert body["attachment_count"] == "1"

    # The upload background task ran and submitted the PDF.
    pdf_submits = [c for c in submit_calls if c.get("source_file_type") == "pdf"]
    assert len(pdf_submits) == 1
    assert pdf_submits[0]["source_file_name"] == "manual.pdf"
    # A success summary DM was sent.
    assert any("Добавлено в базу" in text for _, text in isolated_bot["dms"])


def test_kb_session_continuation_inherits_confidential_flag(isolated_bot, monkeypatch):
    pdf = isolated_bot["tmp_path"] / "secret.pdf"
    pdf.write_bytes(b"%PDF-secret")
    submit_calls: list[dict] = []

    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=pdf, byte_size=11, mime_type=mime_type)

    async def fake_submit(**kwargs):
        submit_calls.append(kwargs)
        return {"inserted_chunks": 1, "is_confidential": True, "deduplicated": False}

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)
    client = TestClient(bot_app)

    # /kb_add confidential opens a confidential session.
    text_msg = _operator_message(text="/kb_add confidential")
    text_msg["update_id"] = 2001
    text_msg["message"]["message_id"] = 2001
    client.post("/telegram/webhook", json=text_msg)

    pdf_msg = _operator_message(
        attachments=[
            {
                "document": {
                    "file_id": "S1",
                    "file_name": "secret.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 11,
                }
            }
        ],
    )
    pdf_msg["update_id"] = 2002
    pdf_msg["message"]["message_id"] = 2002
    client.post("/telegram/webhook", json=pdf_msg)

    pdf_submits = [c for c in submit_calls if c.get("source_file_type") == "pdf"]
    assert len(pdf_submits) == 1
    assert pdf_submits[0]["is_confidential"] is True


def test_kb_session_continuation_refreshes_ttl(isolated_bot, monkeypatch):
    """Each session continuation must extend the TTL so a batch of files
    spread across several minutes keeps working."""
    from datetime import UTC, datetime, timedelta

    pdf = isolated_bot["tmp_path"] / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    async def fake_download(self, *, file_id, suggested_extension, mime_type=None):
        return DownloadedFile(path=pdf, byte_size=4, mime_type=mime_type)

    async def fake_submit(**kwargs):
        return {"inserted_chunks": 1, "is_confidential": False, "deduplicated": False}

    monkeypatch.setattr(bot_main.TelegramFileDownloader, "download", fake_download)
    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)

    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    current = {"t": base}
    monkeypatch.setattr(kb_session_module, "_now", lambda: current["t"])

    client = TestClient(bot_app)
    # T0: open session.
    text_msg = _operator_message(text="хочу добавить материалы в knowledge base")
    text_msg["update_id"] = 3001
    text_msg["message"]["message_id"] = 3001
    client.post("/telegram/webhook", json=text_msg)

    repo = isolated_bot["kb_session_repo"]
    initial = repo.get_active(chat_id=100, username="@ajdevy")
    assert initial is not None
    initial_expires = datetime.fromisoformat(initial.expires_at)
    assert initial_expires == base + timedelta(seconds=600)

    # T0 + 300s: send a PDF. Continuation runs, TTL refreshed to T+900.
    current["t"] = base + timedelta(seconds=300)
    pdf_msg = _operator_message(
        attachments=[
            {
                "document": {
                    "file_id": "P",
                    "file_name": "doc.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 4,
                }
            }
        ],
    )
    pdf_msg["update_id"] = 3002
    pdf_msg["message"]["message_id"] = 3002
    client.post("/telegram/webhook", json=pdf_msg)

    refreshed = repo.get_active(chat_id=100, username="@ajdevy")
    assert refreshed is not None
    refreshed_expires = datetime.fromisoformat(refreshed.expires_at)
    assert refreshed_expires == base + timedelta(seconds=900)


def test_kb_session_continuation_dropped_after_ttl(isolated_bot, monkeypatch):
    """PDF arriving past the TTL window must hit the attachment-only guard."""
    from datetime import UTC, datetime, timedelta

    async def fake_submit(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("submit must not be called for expired session")

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)

    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    current = {"t": base}
    monkeypatch.setattr(kb_session_module, "_now", lambda: current["t"])

    client = TestClient(bot_app)
    # Open session at T0.
    open_msg = _operator_message(text="хочу добавить материалы в knowledge base")
    open_msg["update_id"] = 4001
    open_msg["message"]["message_id"] = 4001
    client.post("/telegram/webhook", json=open_msg)

    # T0 + 601s: PDF arrives after TTL expired.
    current["t"] = base + timedelta(seconds=601)
    pdf_msg = _operator_message(
        attachments=[
            {
                "document": {
                    "file_id": "L",
                    "file_name": "late.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 3,
                }
            }
        ],
    )
    pdf_msg["update_id"] = 4002
    pdf_msg["message"]["message_id"] = 4002
    response = client.post("/telegram/webhook", json=pdf_msg)
    body = response.json()
    assert body["status"] == "ignored"
    assert body["reason"] == "attachment_only"


def test_kb_session_does_not_route_non_operator_attachments(
    isolated_bot, monkeypatch
):
    """A PDF from a different user must NOT be uploaded into the operator's
    open KB session — the session is keyed by (chat_id, username)."""
    async def fake_submit(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("submit must not be called for non-operator")

    monkeypatch.setattr(bot_main.api_client, "submit_operator_upload", fake_submit)

    client = TestClient(bot_app)
    # Operator opens session.
    open_msg = _operator_message(text="хочу добавить материалы в knowledge base")
    open_msg["update_id"] = 5001
    open_msg["message"]["message_id"] = 5001
    client.post("/telegram/webhook", json=open_msg)

    # A non-operator user sends a PDF in the same chat.
    intruder = _operator_message(
        attachments=[
            {
                "document": {
                    "file_id": "Z",
                    "file_name": "rogue.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 5,
                }
            }
        ],
    )
    intruder["update_id"] = 5002
    intruder["message"]["message_id"] = 5002
    intruder["message"]["from"]["username"] = "stranger"
    response = client.post("/telegram/webhook", json=intruder)
    body = response.json()
    assert body["status"] == "ignored"
    assert body["reason"] == "attachment_only"


def test_kb_cancel_from_operator_clears_session(isolated_bot):
    client = TestClient(bot_app)
    # Open session.
    open_msg = _operator_message(text="хочу добавить материалы в knowledge base")
    open_msg["update_id"] = 6001
    open_msg["message"]["message_id"] = 6001
    client.post("/telegram/webhook", json=open_msg)
    repo = isolated_bot["kb_session_repo"]
    assert repo.get_active(chat_id=100, username="@ajdevy") is not None

    # Cancel.
    cancel = _operator_message(text="/kb_cancel")
    cancel["update_id"] = 6002
    cancel["message"]["message_id"] = 6002
    response = client.post("/telegram/webhook", json=cancel)
    body = response.json()
    assert body["status"] == "kb_session_cleared"
    assert repo.get_active(chat_id=100, username="@ajdevy") is None
    assert any("Сессия закрыта" in text for _, text in isolated_bot["dms"])


def test_kb_cancel_from_non_operator_is_unauthorized(isolated_bot):
    client = TestClient(bot_app)
    cancel = _operator_message(text="/kb_cancel")
    cancel["update_id"] = 7001
    cancel["message"]["message_id"] = 7001
    cancel["message"]["from"]["username"] = "stranger"
    response = client.post("/telegram/webhook", json=cancel)
    body = response.json()
    # Non-operator falls through to the customer-forward flow; the
    # `/kb_cancel` check inside _handle_kb_command never runs because the
    # operator gate at the top returns None first.
    assert body.get("kb_mode") is None
    assert body.get("status") != "kb_session_cleared"


def test_kb_continuation_clears_when_attachment_processed_but_session_expires_naturally(
    isolated_bot,
):
    """Sanity: after TTL elapses without continuation, get_active returns
    None even without an explicit clear. (Covered indirectly above; this is
    a positive regression for the repo accessor used by main.py.)"""
    repo = isolated_bot["kb_session_repo"]
    repo.upsert(chat_id=1, username="@op", is_confidential=False, ttl_seconds=0)
    # ttl=0 → expires_at <= now() immediately → considered inactive.
    assert repo.get_active(chat_id=1, username="@op") is None
