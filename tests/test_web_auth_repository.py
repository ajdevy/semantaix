from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.api.app.web_auth import (
    CodeVerification,
    WebAuthRepository,
    WebSession,
)


def _repo(tmp_path: Path) -> WebAuthRepository:
    return WebAuthRepository(db_path=str(tmp_path / "web_auth.db"))


def test_init_schema_creates_tables_and_indexes(tmp_path: Path) -> None:
    _repo(tmp_path)
    with sqlite3.connect(tmp_path / "web_auth.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "web_auth_codes" in tables
    assert "web_sessions" in tables
    assert "idx_web_auth_codes_username_active" in indexes
    assert "idx_web_sessions_username" in indexes


def test_create_code_returns_six_digit_string(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    assert re.fullmatch(r"\d{6}", code)


def test_create_code_supersedes_prior_unconsumed_codes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.create_code(username="@alice", chat_id=100)
    second = repo.create_code(username="@alice", chat_id=100)
    assert first != second
    # The first code can no longer be consumed.
    outcome = repo.consume_code(username="@alice", code=first)
    assert outcome.ok is False
    assert outcome.reason == "invalid"


def test_create_code_does_not_affect_other_username(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code_a = repo.create_code(username="@alice", chat_id=100)
    repo.create_code(username="@bob", chat_id=200)
    outcome = repo.consume_code(username="@alice", code=code_a)
    assert outcome.ok is True


def test_consume_code_success_marks_consumed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    outcome = repo.consume_code(username="@alice", code=code)
    assert outcome.ok is True
    # Cannot reuse.
    again = repo.consume_code(username="@alice", code=code)
    assert again.ok is False
    assert again.reason == "invalid"


def test_consume_code_rejects_unknown_code(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.create_code(username="@alice", chat_id=100)
    outcome = repo.consume_code(username="@alice", code="000000")
    assert outcome.ok is False
    assert outcome.reason == "invalid"


def test_consume_code_rejects_expired(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    # Force expiry in the DB.
    past = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(tmp_path / "web_auth.db") as connection:
        connection.execute(
            "UPDATE web_auth_codes SET expires_at = ? WHERE username = ?",
            (past, "@alice"),
        )
    outcome = repo.consume_code(username="@alice", code=code)
    assert outcome.ok is False
    assert outcome.reason == "expired"


def test_consume_code_after_five_failed_attempts_is_burned(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    for _ in range(5):
        outcome = repo.consume_code(username="@alice", code="999999")
        assert outcome.ok is False
    # The next attempt — even with the correct code — must be rejected.
    final = repo.consume_code(username="@alice", code=code)
    assert final.ok is False
    assert final.reason in {"too_many_attempts", "invalid"}


def test_consume_code_too_many_attempts_returns_remaining_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.create_code(username="@alice", chat_id=100)
    for index in range(4):
        outcome = repo.consume_code(username="@alice", code="999999")
        assert outcome.ok is False
        assert outcome.remaining_attempts == 4 - index
    final = repo.consume_code(username="@alice", code="999999")
    assert final.ok is False
    assert final.remaining_attempts == 0


def test_create_session_returns_unique_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one = repo.create_session(username="@alice", role="operator")
    two = repo.create_session(username="@alice", role="operator")
    assert one != two
    assert len(one) >= 32


def test_get_session_returns_active_session(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    session_id = repo.create_session(username="@alice", role="operator")
    session = repo.get_session(session_id=session_id)
    assert isinstance(session, WebSession)
    assert session.username == "@alice"
    assert session.role == "operator"
    assert session.revoked_at is None


def test_get_session_unknown_returns_none(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert repo.get_session(session_id="nope") is None


def test_get_session_revoked_returns_none(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    session_id = repo.create_session(username="@alice", role="operator")
    repo.revoke_session(session_id=session_id)
    assert repo.get_session(session_id=session_id) is None


def test_revoke_all_for_username_revokes_active_sessions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    a1 = repo.create_session(username="@alice", role="operator")
    a2 = repo.create_session(username="@alice", role="operator")
    bob = repo.create_session(username="@bob", role="operator")
    repo.revoke_all_for_username(username="@alice")
    assert repo.get_session(session_id=a1) is None
    assert repo.get_session(session_id=a2) is None
    assert repo.get_session(session_id=bob) is not None


def test_touch_session_updates_last_seen(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    session_id = repo.create_session(username="@alice", role="operator")
    initial = repo.get_session(session_id=session_id)
    assert initial is not None
    import time

    time.sleep(0.01)
    repo.touch_session(session_id=session_id)
    refreshed = repo.get_session(session_id=session_id)
    assert refreshed is not None
    assert refreshed.last_seen_at != initial.last_seen_at


def test_code_verification_dataclass_defaults() -> None:
    cv = CodeVerification(ok=True, reason=None, remaining_attempts=5, chat_id=42)
    assert cv.ok is True
    assert cv.reason is None
    assert cv.remaining_attempts == 5
    assert cv.chat_id == 42


def test_wal_journal_mode_enabled(tmp_path: Path) -> None:
    _repo(tmp_path)
    with sqlite3.connect(tmp_path / "web_auth.db") as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_revoke_session_unknown_id_is_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.revoke_session(session_id="nonexistent")


def test_touch_session_unknown_id_is_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.touch_session(session_id="nonexistent")


def test_consume_code_username_mismatch_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    outcome = repo.consume_code(username="@bob", code=code)
    assert outcome.ok is False
    assert outcome.reason == "invalid"


def test_consume_code_returns_chat_id_on_success(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=4242)
    outcome = repo.consume_code(username="@alice", code=code)
    assert outcome.ok is True
    assert outcome.chat_id == 4242


def test_code_is_zero_padded_when_small_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.api.app.web_auth as web_auth_module

    monkeypatch.setattr(
        web_auth_module.secrets, "randbelow", lambda _bound: 7
    )
    repo = _repo(tmp_path)
    code = repo.create_code(username="@alice", chat_id=100)
    assert code == "000007"
