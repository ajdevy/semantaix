import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app.admin_nl_ops import (
    OP_CLARIFY,
    OP_FILE_ATTACH,
    OP_OPERATOR_ATTACH,
    OP_OPERATOR_DETACH,
    OP_PROJECT_CREATE,
    OP_PROJECT_RENAME,
    STATUS_CANCELLED,
    STATUS_CLARIFY,
    STATUS_CONFIRMED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    AdminNlOpsRepository,
    InvalidConfirmToken,
    SessionNotPending,
    parse_intent,
)


def test_init_schema_creates_table(tmp_path):
    path = str(tmp_path / "nl.sqlite3")
    AdminNlOpsRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert "admin_nl_op_sessions" in {row[0] for row in rows}


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "nl.sqlite3")
    AdminNlOpsRepository(path).init_schema()
    AdminNlOpsRepository(path).init_schema()


def test_init_schema_adds_preview_to_legacy_table(tmp_path):
    """Legacy schema from 10.01 lacked the preview column; ALTER fills it."""
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE admin_nl_op_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_username TEXT NOT NULL,
                utterance TEXT NOT NULL,
                op_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                confirm_token TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    AdminNlOpsRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(admin_nl_op_sessions)"
        ).fetchall()
    assert "preview" in {row[1] for row in rows}


def test_parse_project_create():
    intent = parse_intent("создай проект billing Биллинг команда")
    assert intent.op_type == OP_PROJECT_CREATE
    assert intent.payload == {"slug": "billing", "name": "Биллинг команда"}


def test_parse_project_create_without_explicit_name():
    intent = parse_intent("Создай проект qa")
    assert intent.op_type == OP_PROJECT_CREATE
    assert intent.payload == {"slug": "qa", "name": "qa"}


def test_parse_project_rename():
    intent = parse_intent("переименуй проект billing в Финансы")
    assert intent.op_type == OP_PROJECT_RENAME
    assert intent.payload == {"slug": "billing", "name": "Финансы"}


def test_parse_operator_attach_with_chat_id():
    intent = parse_intent("добавь оператора @op-b в billing 12345")
    assert intent.op_type == OP_OPERATOR_ATTACH
    assert intent.payload == {
        "username": "@op-b",
        "project_slug": "billing",
        "chat_id": 12345,
    }


def test_parse_operator_attach_without_chat_id():
    intent = parse_intent("Добавьте оператора @op-c в billing")
    assert intent.op_type == OP_OPERATOR_ATTACH
    assert "chat_id" not in intent.payload


def test_parse_operator_detach():
    intent = parse_intent("удали оператора @op-x")
    assert intent.op_type == OP_OPERATOR_DETACH
    assert intent.payload == {"username": "@op-x"}


def test_parse_file_attach():
    intent = parse_intent("привяжи файл #ABC123 к billing")
    assert intent.op_type == OP_FILE_ATTACH
    assert intent.payload == {"short_id": "ABC123", "project_slug": "billing"}


def test_parse_unknown_returns_clarify():
    intent = parse_intent("что-то непонятное")
    assert intent.op_type == OP_CLARIFY
    assert "Попробуйте" in intent.preview


def test_propose_recognized_returns_pending_with_token(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    assert session.status == STATUS_PENDING
    assert session.op_type == OP_PROJECT_CREATE
    assert session.confirm_token
    assert session.preview.startswith("Создать проект")


def test_propose_unknown_returns_clarify_without_token(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(admin_username="@admin", utterance="???")
    assert session.status == STATUS_CLARIFY
    assert session.confirm_token is None


def test_confirm_round_trip(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    confirmed = repo.confirm(
        session_id=session.id, confirm_token=session.confirm_token or ""
    )
    assert confirmed.status == STATUS_CONFIRMED
    assert confirmed.confirm_token is None


def test_confirm_wrong_token_rejected(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    with pytest.raises(InvalidConfirmToken):
        repo.confirm(session_id=session.id, confirm_token="wrong")


def test_confirm_clarify_session_rejected(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(admin_username="@admin", utterance="???")
    with pytest.raises(SessionNotPending):
        repo.confirm(session_id=session.id, confirm_token="any")


def test_confirm_replay_rejected(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    repo.confirm(
        session_id=session.id, confirm_token=session.confirm_token or ""
    )
    with pytest.raises(SessionNotPending):
        repo.confirm(
            session_id=session.id, confirm_token=session.confirm_token or ""
        )


def test_cancel_pending(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    cancelled = repo.cancel(session_id=session.id)
    assert cancelled.status == STATUS_CANCELLED


def test_cancel_clarify(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(admin_username="@admin", utterance="???")
    cancelled = repo.cancel(session_id=session.id)
    assert cancelled.status == STATUS_CANCELLED


def test_cancel_already_confirmed_rejected(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    repo.confirm(
        session_id=session.id, confirm_token=session.confirm_token or ""
    )
    with pytest.raises(SessionNotPending):
        repo.cancel(session_id=session.id)


def test_get_unknown_session_raises(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    with pytest.raises(LookupError):
        repo.get(999)


def test_expired_session_auto_marks(tmp_path):
    repo = AdminNlOpsRepository(
        str(tmp_path / "nl.sqlite3"), pending_ttl_seconds=1
    )
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    # Backdate creation by 2 seconds.
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(repo.db_path) as connection:
        connection.execute(
            "UPDATE admin_nl_op_sessions SET created_at = ? WHERE id = ?",
            (past, session.id),
        )
    refreshed = repo.get(session.id)
    assert refreshed.status == STATUS_EXPIRED


def test_expired_session_cannot_be_confirmed(tmp_path):
    repo = AdminNlOpsRepository(
        str(tmp_path / "nl.sqlite3"), pending_ttl_seconds=1
    )
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(repo.db_path) as connection:
        connection.execute(
            "UPDATE admin_nl_op_sessions SET created_at = ? WHERE id = ?",
            (past, session.id),
        )
    with pytest.raises(SessionNotPending):
        repo.confirm(
            session_id=session.id, confirm_token=session.confirm_token or ""
        )


def test_latest_pending_for(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    a = repo.propose(
        admin_username="@admin", utterance="создай проект a A"
    )
    b = repo.propose(
        admin_username="@admin", utterance="создай проект b B"
    )
    latest = repo.latest_pending_for("@admin")
    assert latest is not None
    assert latest.id == b.id
    # After cancelling the latest, the next-latest pending takes its place.
    repo.cancel(session_id=b.id)
    latest2 = repo.latest_pending_for("@admin")
    assert latest2 is not None
    assert latest2.id == a.id


def test_latest_pending_for_returns_none_when_no_pending(tmp_path):
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    assert repo.latest_pending_for("@admin") is None
