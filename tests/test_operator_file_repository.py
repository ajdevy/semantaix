from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from services.bot_gateway.app.operator_files import (
    OperatorFileRecord,
    OperatorFileRepository,
)
from services.bot_gateway.app.telegram_update import TelegramAttachment

SHORT_ID_RE = re.compile(r"^[A-Z2-9]{8}$")


def _attachment(
    *,
    file_id: str = "tg-file-id-1",
    name: str | None = "doc.pdf",
    mime: str | None = "application/pdf",
    size: int | None = 1234,
) -> TelegramAttachment:
    return TelegramAttachment(
        file_id=file_id,
        kind="document",
        mime_type=mime,
        file_size=size,
        file_name=name,
    )


def _repo(tmp_path: Path) -> OperatorFileRepository:
    return OperatorFileRepository(db_path=str(tmp_path / "files.db"))


def test_record_upload_persists_all_fields(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = repo.record_upload(
        chat_id=42,
        username="@op",
        source_message_id=7,
        attachment=_attachment(name="brochure.pdf"),
        is_confidential=True,
        stored_binary_path="/tmp/x.pdf",
        download_status="ok",
        source_file_type="pdf",
        kb_ingest_status="pending",
    )
    assert isinstance(record, OperatorFileRecord)
    assert SHORT_ID_RE.match(record.short_id)
    assert record.telegram_file_id == "tg-file-id-1"
    assert record.chat_id == 42
    assert record.username == "@op"
    assert record.source_message_id == 7
    assert record.source_file_name == "brochure.pdf"
    assert record.source_file_type == "pdf"
    assert record.mime_type == "application/pdf"
    assert record.file_size_bytes == 1234
    assert record.is_confidential is True
    assert record.stored_binary_path == "/tmp/x.pdf"
    assert record.download_status == "ok"
    assert record.kb_ingest_status == "pending"
    assert record.kb_inserted_chunks is None
    datetime.fromisoformat(record.created_at)
    datetime.fromisoformat(record.updated_at)


def test_short_id_unique_across_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    seen: set[str] = set()
    for i in range(50):
        record = repo.record_upload(
            chat_id=1,
            username="@op",
            source_message_id=i,
            attachment=_attachment(file_id=f"f{i}", name=f"a{i}.pdf"),
            is_confidential=False,
            stored_binary_path=None,
            download_status="ok",
            source_file_type="pdf",
        )
        assert record.short_id not in seen
        seen.add(record.short_id)


def test_record_retries_on_short_id_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    first = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=1,
        attachment=_attachment(file_id="f1"),
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="pdf",
    )
    collide = first.short_id

    from services.bot_gateway.app import operator_files

    sequence = iter([collide, collide, "ZZZZZZZ9"])
    monkeypatch.setattr(
        operator_files, "_generate_short_id", lambda: next(sequence)
    )
    second = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=2,
        attachment=_attachment(file_id="f2"),
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="pdf",
    )
    assert second.short_id == "ZZZZZZZ9"


def test_record_short_id_collision_exhausted_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    first = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=1,
        attachment=_attachment(file_id="f1"),
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="pdf",
    )
    collide = first.short_id

    from services.bot_gateway.app import operator_files

    monkeypatch.setattr(operator_files, "_generate_short_id", lambda: collide)
    with pytest.raises(RuntimeError):
        repo.record_upload(
            chat_id=1,
            username="@op",
            source_message_id=2,
            attachment=_attachment(file_id="f2"),
            is_confidential=False,
            stored_binary_path=None,
            download_status="ok",
            source_file_type="pdf",
        )


def test_update_kb_status_bumps_updated_at(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=1,
        attachment=_attachment(file_id="f1"),
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="pdf",
    )
    initial_updated_at = record.updated_at

    import time

    time.sleep(0.005)
    repo.update_kb_status(
        short_id=record.short_id, kb_ingest_status="ok", kb_inserted_chunks=12
    )
    refreshed = repo.get(short_id=record.short_id)
    assert refreshed is not None
    assert refreshed.kb_ingest_status == "ok"
    assert refreshed.kb_inserted_chunks == 12
    assert refreshed.created_at == record.created_at
    assert refreshed.updated_at != initial_updated_at


def test_update_kb_status_unknown_short_id_is_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.update_kb_status(
        short_id="UNKNOWN1", kb_ingest_status="ok", kb_inserted_chunks=0
    )


def test_list_recent_returns_newest_first_scoped_to_username(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    for i in range(3):
        repo.record_upload(
            chat_id=1,
            username="@alice",
            source_message_id=i,
            attachment=_attachment(file_id=f"a{i}", name=f"alice_{i}.pdf"),
            is_confidential=False,
            stored_binary_path=None,
            download_status="ok",
            source_file_type="pdf",
        )
    for i in range(2):
        repo.record_upload(
            chat_id=2,
            username="@bob",
            source_message_id=i,
            attachment=_attachment(file_id=f"b{i}", name=f"bob_{i}.pdf"),
            is_confidential=False,
            stored_binary_path=None,
            download_status="ok",
            source_file_type="pdf",
        )

    alice = repo.list_recent(username="@alice", limit=10)
    assert [r.username for r in alice] == ["@alice", "@alice", "@alice"]
    assert [r.source_file_name for r in alice] == [
        "alice_2.pdf",
        "alice_1.pdf",
        "alice_0.pdf",
    ]
    bob = repo.list_recent(username="@bob", limit=10)
    assert len(bob) == 2
    assert all(r.username == "@bob" for r in bob)


def test_list_recent_respects_limit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for i in range(5):
        repo.record_upload(
            chat_id=1,
            username="@op",
            source_message_id=i,
            attachment=_attachment(file_id=f"f{i}", name=f"n{i}.pdf"),
            is_confidential=False,
            stored_binary_path=None,
            download_status="ok",
            source_file_type="pdf",
        )
    assert len(repo.list_recent(username="@op", limit=2)) == 2


def test_get_returns_none_for_unknown(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert repo.get(short_id="ZZZZZZ99") is None


def test_get_returns_full_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=1,
        attachment=_attachment(file_id="f1", name="x.pdf"),
        is_confidential=True,
        stored_binary_path="/p",
        download_status="too_large",
        source_file_type="pdf",
        kb_ingest_status="skipped",
    )
    fetched = repo.get(short_id=record.short_id)
    assert fetched is not None
    assert fetched.short_id == record.short_id
    assert fetched.is_confidential is True
    assert fetched.download_status == "too_large"
    assert fetched.kb_ingest_status == "skipped"
    assert fetched.stored_binary_path == "/p"


def test_schema_initialised_on_construction(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    OperatorFileRepository(db_path=str(db_path))
    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        idx_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "operator_files" in names
    assert "operator_files_username_created_idx" in idx_names


def test_short_id_uses_unambiguous_alphabet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.bot_gateway.app import operator_files

    samples = [operator_files._generate_short_id() for _ in range(200)]
    pattern = re.compile(r"^[A-Z2-9]{8}$")
    for sample in samples:
        assert pattern.match(sample), sample
    assert all("0" not in s and "1" not in s for s in samples)
    assert all("O" not in s and "I" not in s for s in samples)


def test_record_upload_accepts_missing_size_and_mime(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = repo.record_upload(
        chat_id=1,
        username="@op",
        source_message_id=1,
        attachment=_attachment(file_id="f1", mime=None, size=None, name=None),
        is_confidential=False,
        stored_binary_path=None,
        download_status="failed:telegram_network_error",
        source_file_type="pdf",
        kb_ingest_status="skipped",
    )
    assert record.file_size_bytes is None
    assert record.mime_type is None
    assert record.source_file_name is None
