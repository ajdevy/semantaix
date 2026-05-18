"""Unit tests for OperatorFilesAdminWriter (cross-DB cascade delete)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.api.app.knowledge_moderation import KnowledgeModerationRepository
from services.api.app.operator_files_admin import OperatorFilesAdminWriter
from services.api.app.rag import RagRepository
from services.bot_gateway.app.operator_files import OperatorFileRepository
from services.bot_gateway.app.telegram_update import TelegramAttachment


def _attach(name: str = "x.pdf") -> TelegramAttachment:
    return TelegramAttachment(
        file_id="tg-" + name,
        kind="document",
        mime_type="application/pdf",
        file_size=100,
        file_name=name,
    )


@pytest.fixture
def writer_env(tmp_path: Path) -> dict[str, object]:
    op_files_db = tmp_path / "op_files.db"
    knowledge_db = tmp_path / "knowledge.db"
    rag_db = tmp_path / "rag.db"
    files_repo = OperatorFileRepository(db_path=str(op_files_db))
    moderation_repo = KnowledgeModerationRepository(db_path=str(knowledge_db))
    rag_repo = RagRepository(db_path=str(rag_db))
    writer = OperatorFilesAdminWriter(
        operator_files_db_path=str(op_files_db),
        knowledge_db_path=str(knowledge_db),
        rag_db_path=str(rag_db),
    )
    return {
        "writer": writer,
        "files_repo": files_repo,
        "moderation_repo": moderation_repo,
        "rag_repo": rag_repo,
        "tmp_path": tmp_path,
    }


def _seed(
    env: dict[str, object],
    *,
    username: str,
    name: str,
    text: str,
    link: bool = True,
    binary: Path | None = None,
) -> tuple[str, int | None]:
    files_repo: OperatorFileRepository = env["files_repo"]  # type: ignore[assignment]
    moderation_repo: KnowledgeModerationRepository = env["moderation_repo"]  # type: ignore[assignment]
    rag_repo: RagRepository = env["rag_repo"]  # type: ignore[assignment]
    candidate = moderation_repo.create_pending(text=text)
    binary_path: str | None = None
    if binary is not None:
        binary.write_bytes(b"bin")
        binary_path = str(binary)
    record = files_repo.record_upload(
        chat_id=100,
        username=username,
        source_message_id=1,
        attachment=_attach(name=name),
        is_confidential=False,
        stored_binary_path=binary_path,
        download_status="ok",
        source_file_type="pdf",
        kb_ingest_status="ok",
        kb_inserted_chunks=2,
    )
    candidate_id: int | None = None
    if link:
        files_repo.set_candidate_id(
            short_id=record.short_id, knowledge_candidate_id=candidate.id
        )
        rag_repo.ingest(
            source_id=f"knowledge_candidate:{candidate.id}",
            text=text,
        )
        candidate_id = candidate.id
    return record.short_id, candidate_id


def test_delete_full_cascade_for_operator_own_file(writer_env: dict[str, object]) -> None:
    binary = writer_env["tmp_path"] / "alice.bin"  # type: ignore[operator]
    short_id, candidate_id = _seed(
        writer_env,
        username="@alice",
        name="alice.pdf",
        text="alice content\nsecond line",
        binary=binary,
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is not None
    assert summary.deleted_files == 1
    assert summary.deleted_candidates == 1
    assert summary.deleted_chunks == 2
    assert summary.deleted_binaries == 1
    assert summary.failed_binary_paths == []
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    moderation_repo: KnowledgeModerationRepository = writer_env["moderation_repo"]  # type: ignore[assignment]
    assert files_repo.get(short_id=short_id) is None
    assert candidate_id is not None
    with pytest.raises(LookupError):
        moderation_repo.get(candidate_id)
    assert not binary.exists()


def test_delete_returns_none_for_other_owner_when_operator(
    writer_env: dict[str, object],
) -> None:
    short_id, _ = _seed(
        writer_env, username="@bob", name="bob.pdf", text="bob content"
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is None
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    assert files_repo.get(short_id=short_id) is not None


def test_delete_for_admin_succeeds_on_other_owner(
    writer_env: dict[str, object],
) -> None:
    short_id, _ = _seed(
        writer_env, username="@bob", name="bob.pdf", text="bob content"
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@admin", viewer_role="admin"
    )
    assert summary is not None
    assert summary.deleted_files == 1
    assert summary.deleted_candidates == 1


def test_delete_unknown_short_id_returns_none(writer_env: dict[str, object]) -> None:
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    assert (
        writer.delete(
            short_id="NOSUCH00",
            viewer_username="@admin",
            viewer_role="admin",
        )
        is None
    )


def test_delete_with_null_binary_path(writer_env: dict[str, object]) -> None:
    short_id, _ = _seed(
        writer_env, username="@alice", name="x.pdf", text="x", binary=None
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is not None
    assert summary.deleted_binaries == 0
    assert summary.failed_binary_paths == []


def test_delete_with_null_candidate_link(writer_env: dict[str, object]) -> None:
    short_id, _ = _seed(
        writer_env,
        username="@alice",
        name="orphan.pdf",
        text="orphan",
        link=False,
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is not None
    assert summary.deleted_files == 1
    assert summary.deleted_candidates == 0
    assert summary.deleted_chunks == 0


def test_delete_unlink_failure_recorded_in_summary(
    writer_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = writer_env["tmp_path"] / "stuck.bin"  # type: ignore[operator]
    short_id, _ = _seed(
        writer_env,
        username="@alice",
        name="stuck.pdf",
        text="stuck content",
        binary=binary,
    )

    original_unlink = os.unlink

    def fake_unlink(path: str) -> None:
        if str(path) == str(binary):
            raise PermissionError("locked")
        original_unlink(path)

    monkeypatch.setattr("services.api.app.operator_files_admin.os.unlink", fake_unlink)
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is not None
    assert summary.failed_binary_paths == [str(binary)]
    assert summary.deleted_binaries == 0
    # DB cascade still committed.
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    assert files_repo.get(short_id=short_id) is None


def test_delete_unlink_treats_missing_file_as_success(
    writer_env: dict[str, object],
) -> None:
    # Path that points nowhere on disk.
    short_id, _ = _seed(
        writer_env, username="@alice", name="ghost.pdf", text="ghost"
    )
    # Force a stored_binary_path that doesn't exist.
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    import sqlite3

    with sqlite3.connect(files_repo.db_path) as connection:
        connection.execute(
            "UPDATE operator_files SET stored_binary_path = ? WHERE short_id = ?",
            ("/nonexistent/path.bin", short_id),
        )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete(
        short_id=short_id, viewer_username="@alice", viewer_role="operator"
    )
    assert summary is not None
    assert summary.deleted_binaries == 1
    assert summary.failed_binary_paths == []


def test_delete_all_for_user_only_touches_own_rows(
    writer_env: dict[str, object],
) -> None:
    for i in range(3):
        _seed(
            writer_env,
            username="@alice",
            name=f"alice_{i}.pdf",
            text=f"alice {i} content",
        )
    bob_short_id, _ = _seed(
        writer_env, username="@bob", name="bob.pdf", text="bob content"
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete_all_for_user(username="@alice")
    assert summary.deleted_files == 3
    assert summary.deleted_candidates == 3
    assert summary.deleted_chunks == 3
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    assert files_repo.get(short_id=bob_short_id) is not None


def test_delete_all_returns_zero_summary_when_no_files(
    writer_env: dict[str, object],
) -> None:
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    summary = writer.delete_all_for_user(username="@nobody")
    assert summary.deleted_files == 0
    assert summary.deleted_chunks == 0
    assert summary.deleted_candidates == 0
    assert summary.deleted_binaries == 0
    assert summary.failed_binary_paths == []


def test_delete_raises_when_db_path_missing(tmp_path: Path) -> None:
    writer = OperatorFilesAdminWriter(
        operator_files_db_path=str(tmp_path / "nope.db"),
        knowledge_db_path=str(tmp_path / "k.db"),
        rag_db_path=str(tmp_path / "r.db"),
    )
    with pytest.raises(FileNotFoundError):
        writer.delete(short_id="X", viewer_username="@a", viewer_role="operator")


def test_delete_all_cascade_rolls_back_on_failure(
    writer_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(writer_env, username="@alice", name="a.pdf", text="alice 1")
    _seed(writer_env, username="@alice", name="b.pdf", text="alice 2")

    from services.api.app import operator_files_admin as ofa

    original_cascade = ofa.OperatorFilesAdminWriter._cascade_delete

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("simulated_bulk_failure")

    monkeypatch.setattr(
        ofa.OperatorFilesAdminWriter, "_cascade_delete", staticmethod(boom)
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="simulated_bulk_failure"):
        writer.delete_all_for_user(username="@alice")
    # Restore so the assertion sees the un-rolled-back state.
    monkeypatch.setattr(
        ofa.OperatorFilesAdminWriter,
        "_cascade_delete",
        staticmethod(original_cascade),
    )
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    remaining = files_repo.list_recent(username="@alice", limit=10)
    assert len(remaining) == 2


def test_delete_cascade_rolls_back_on_failure(
    writer_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_id, _ = _seed(
        writer_env, username="@alice", name="x.pdf", text="x content"
    )
    # Inject a failure inside the transaction (after the SELECT, before COMMIT)
    # by patching _cascade_delete to raise.
    from services.api.app import operator_files_admin as ofa

    original_cascade = ofa.OperatorFilesAdminWriter._cascade_delete

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("simulated_disk_full")

    monkeypatch.setattr(
        ofa.OperatorFilesAdminWriter, "_cascade_delete", staticmethod(boom)
    )
    writer: OperatorFilesAdminWriter = writer_env["writer"]  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="simulated_disk_full"):
        writer.delete(
            short_id=short_id,
            viewer_username="@alice",
            viewer_role="operator",
        )
    # Restore for the assertion that the row survived rollback.
    monkeypatch.setattr(
        ofa.OperatorFilesAdminWriter,
        "_cascade_delete",
        staticmethod(original_cascade),
    )
    files_repo: OperatorFileRepository = writer_env["files_repo"]  # type: ignore[assignment]
    assert files_repo.get(short_id=short_id) is not None
