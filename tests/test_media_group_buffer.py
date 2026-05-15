from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.bot_gateway.app.media_group_buffer import (
    BufferedAttachment,
    MediaGroupBuffer,
)
from services.bot_gateway.app.telegram_update import TelegramAttachment


def _attachment(file_id: str = "f1", name: str = "a.pdf") -> TelegramAttachment:
    return TelegramAttachment(
        file_id=file_id,
        kind="document",
        mime_type="application/pdf",
        file_size=1234,
        file_name=name,
    )


def _make_buffer(tmp_path: Path) -> MediaGroupBuffer:
    return MediaGroupBuffer(db_path=str(tmp_path / "buffer.db"))


def test_first_add_returns_true_subsequent_returns_false(tmp_path: Path) -> None:
    buf = _make_buffer(tmp_path)
    first = buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=100,
        source_message_id=200,
        attachment=_attachment("f-a", "a.pdf"),
        is_confidential=False,
    )
    second = buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=101,
        source_message_id=201,
        attachment=_attachment("f-b", "b.pdf"),
        is_confidential=False,
    )
    assert first is True
    assert second is False


def test_duplicate_update_id_does_not_double_insert(tmp_path: Path) -> None:
    buf = _make_buffer(tmp_path)
    first = buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=100,
        source_message_id=200,
        attachment=_attachment("f-a"),
        is_confidential=False,
    )
    again = buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=100,
        source_message_id=200,
        attachment=_attachment("f-a"),
        is_confidential=False,
    )
    assert first is True
    assert again is False
    drained = buf.drain(media_group_id="MG1")
    assert len(drained) == 1


def test_drain_returns_attachments_in_insertion_order_and_removes(
    tmp_path: Path,
) -> None:
    buf = _make_buffer(tmp_path)
    buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=100,
        source_message_id=200,
        attachment=_attachment("f-a", "first.pdf"),
        is_confidential=False,
    )
    buf.add(
        media_group_id="MG1",
        chat_id=10,
        username="@op",
        update_id=101,
        source_message_id=201,
        attachment=_attachment("f-b", "second.pdf"),
        is_confidential=True,
    )
    drained = buf.drain(media_group_id="MG1")
    assert [d.attachment.file_id for d in drained] == ["f-a", "f-b"]
    assert [d.attachment.file_name for d in drained] == ["first.pdf", "second.pdf"]
    assert [d.is_confidential for d in drained] == [False, True]
    assert [d.update_id for d in drained] == [100, 101]
    assert [d.source_message_id for d in drained] == [200, 201]
    assert drained[0].chat_id == 10
    assert drained[0].username == "@op"
    assert isinstance(drained[0], BufferedAttachment)
    assert buf.drain(media_group_id="MG1") == []


def test_drain_missing_group_returns_empty_list(tmp_path: Path) -> None:
    buf = _make_buffer(tmp_path)
    assert buf.drain(media_group_id="missing") == []


def test_independent_groups_do_not_interfere(tmp_path: Path) -> None:
    buf = _make_buffer(tmp_path)
    first_in_a = buf.add(
        media_group_id="A",
        chat_id=10,
        username="@op",
        update_id=1,
        source_message_id=1,
        attachment=_attachment("a"),
        is_confidential=False,
    )
    first_in_b = buf.add(
        media_group_id="B",
        chat_id=10,
        username="@op",
        update_id=2,
        source_message_id=2,
        attachment=_attachment("b"),
        is_confidential=False,
    )
    assert first_in_a is True
    assert first_in_b is True
    assert [d.attachment.file_id for d in buf.drain(media_group_id="A")] == ["a"]
    assert [d.attachment.file_id for d in buf.drain(media_group_id="B")] == ["b"]


def test_schema_initialised_on_construction(tmp_path: Path) -> None:
    db_path = tmp_path / "buf.db"
    MediaGroupBuffer(db_path=str(db_path))
    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "operator_media_group_buffer" in names


@pytest.mark.asyncio
async def test_concurrent_add_only_one_returns_true(tmp_path: Path) -> None:
    import asyncio

    buf = _make_buffer(tmp_path)

    async def add(update_id: int) -> bool:
        return await asyncio.to_thread(
            buf.add,
            media_group_id="MG_RACE",
            chat_id=10,
            username="@op",
            update_id=update_id,
            source_message_id=update_id,
            attachment=_attachment(f"f-{update_id}"),
            is_confidential=False,
        )

    results = await asyncio.gather(*(add(i) for i in range(8)))
    assert sum(1 for r in results if r) == 1
    assert len(buf.drain(media_group_id="MG_RACE")) == 8
