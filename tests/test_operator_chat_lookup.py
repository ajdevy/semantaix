from __future__ import annotations

from pathlib import Path

from services.api.app.hitl import HitlTicketRepository
from services.api.app.operator_chat_lookup import resolve_chat_id_for_username
from services.bot_gateway.app.operator_files import OperatorFileRepository
from services.bot_gateway.app.telegram_update import TelegramAttachment


def _attachment(file_id: str = "f1") -> TelegramAttachment:
    return TelegramAttachment(
        file_id=file_id,
        kind="document",
        mime_type="application/pdf",
        file_size=10,
        file_name="x.pdf",
    )


def _seed_operator_file(repo: OperatorFileRepository, *, chat_id: int, username: str) -> None:
    repo.record_upload(
        chat_id=chat_id,
        username=username,
        source_message_id=1,
        attachment=_attachment(),
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="pdf",
    )


def test_resolves_from_latest_operator_files_row(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    repo = OperatorFileRepository(db_path=str(operator_files_db))
    _seed_operator_file(repo, chat_id=4242, username="@alice")
    result = resolve_chat_id_for_username(
        username="@alice",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=None,
        primary_operator_username="@ajdevy",
    )
    assert result == 4242


def test_returns_latest_chat_id_when_multiple_rows(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    repo = OperatorFileRepository(db_path=str(operator_files_db))
    _seed_operator_file(repo, chat_id=1, username="@alice")
    _seed_operator_file(repo, chat_id=2, username="@alice")
    _seed_operator_file(repo, chat_id=3, username="@alice")
    result = resolve_chat_id_for_username(
        username="@alice",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=None,
        primary_operator_username="@ajdevy",
    )
    assert result == 3


def test_falls_back_to_runtime_config_for_primary_operator(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    OperatorFileRepository(db_path=str(operator_files_db))  # init only
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    hitl_repo.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value="9999",
        updated_by="test",
    )
    result = resolve_chat_id_for_username(
        username="@ajdevy",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result == 9999


def test_runtime_config_not_used_for_non_primary_operator(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    OperatorFileRepository(db_path=str(operator_files_db))
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    hitl_repo.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value="9999",
        updated_by="test",
    )
    result = resolve_chat_id_for_username(
        username="@stranger",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result is None


def test_returns_none_when_no_signal(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    OperatorFileRepository(db_path=str(operator_files_db))
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    result = resolve_chat_id_for_username(
        username="@alice",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result is None


def test_operator_files_takes_precedence_over_runtime_config(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    repo = OperatorFileRepository(db_path=str(operator_files_db))
    _seed_operator_file(repo, chat_id=555, username="@ajdevy")
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    hitl_repo.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value="9999",
        updated_by="test",
    )
    result = resolve_chat_id_for_username(
        username="@ajdevy",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result == 555


def test_returns_none_if_runtime_config_is_invalid_integer(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    OperatorFileRepository(db_path=str(operator_files_db))
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    hitl_repo.set_runtime_config(
        key="hitl_primary_operator_chat_id",
        value="not-a-number",
        updated_by="test",
    )
    result = resolve_chat_id_for_username(
        username="@ajdevy",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result is None


def test_primary_operator_without_runtime_config_returns_none(tmp_path: Path) -> None:
    operator_files_db = tmp_path / "op_files.db"
    OperatorFileRepository(db_path=str(operator_files_db))
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    # No runtime config set — primary user with no files and no config = None.
    result = resolve_chat_id_for_username(
        username="@ajdevy",
        operator_files_db_path=str(operator_files_db),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result is None


def test_handles_missing_operator_files_db_gracefully(tmp_path: Path) -> None:
    missing_path = tmp_path / "nope.db"
    hitl_repo = HitlTicketRepository(db_path=str(tmp_path / "hitl.db"))
    result = resolve_chat_id_for_username(
        username="@alice",
        operator_files_db_path=str(missing_path),
        hitl_repository=hitl_repo,
        primary_operator_username="@ajdevy",
    )
    assert result is None
