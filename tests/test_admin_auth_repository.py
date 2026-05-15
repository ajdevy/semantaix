import hashlib
import re
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app.admin_auth import (
    AdminAuthRepository,
    AdminSession,
    InvalidLoginCode,
)


def test_init_schema_creates_both_tables(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    AdminAuthRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {row[0] for row in rows}
    assert "admin_login_codes" in names
    assert "admin_sessions" in names


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    repository.init_schema()
    repository.init_schema()
    with sqlite3.connect(path) as connection:
        codes = connection.execute(
            "PRAGMA table_info(admin_login_codes)"
        ).fetchall()
        sessions = connection.execute(
            "PRAGMA table_info(admin_sessions)"
        ).fetchall()
    code_columns = {row[1] for row in codes}
    session_columns = {row[1] for row in sessions}
    assert {
        "id",
        "admin_username",
        "code_sha256",
        "expires_at",
        "consumed_at",
        "created_at",
    }.issubset(code_columns)
    assert {
        "token_sha256",
        "admin_username",
        "expires_at",
        "created_at",
    }.issubset(session_columns)


def test_init_schema_creates_index_on_username(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    AdminAuthRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    names = {row[0] for row in rows}
    assert "idx_admin_codes_username" in names


def test_request_code_returns_six_digit_plaintext(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    assert re.fullmatch(r"\d{6}", code)


def test_request_code_stores_sha256_not_plaintext(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT code_sha256 FROM admin_login_codes WHERE admin_username = ?",
            ("@admin",),
        ).fetchall()
    assert len(rows) == 1
    stored = rows[0][0]
    assert stored != code
    assert stored == hashlib.sha256(code.encode("utf-8")).hexdigest()


def test_request_code_invalidates_prior_unconsumed_codes(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    repository.request_code(admin_username="@admin", ttl_seconds=300)
    repository.request_code(admin_username="@admin", ttl_seconds=300)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT consumed_at FROM admin_login_codes "
            "WHERE admin_username = ? ORDER BY id ASC",
            ("@admin",),
        ).fetchall()
    assert len(rows) == 2
    # First row consumed (invalidated), second still active.
    assert rows[0][0] is not None
    assert rows[1][0] is None


def test_request_code_does_not_invalidate_other_admins(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    repository.request_code(admin_username="@a", ttl_seconds=300)
    repository.request_code(admin_username="@b", ttl_seconds=300)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT admin_username, consumed_at FROM admin_login_codes "
            "ORDER BY id ASC"
        ).fetchall()
    assert rows[0] == ("@a", None)
    assert rows[1] == ("@b", None)


def test_consume_code_round_trip_returns_session(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    session = repository.consume_code(
        admin_username="@admin", code=code, ttl_seconds=86400
    )
    assert isinstance(session, AdminSession)
    assert session.admin_username == "@admin"
    assert session.token  # plaintext token returned
    assert session.expires_at


def test_consume_code_marks_consumed(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    repository.consume_code(admin_username="@admin", code=code, ttl_seconds=86400)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT consumed_at FROM admin_login_codes WHERE admin_username = ?",
            ("@admin",),
        ).fetchone()
    assert row[0] is not None


def test_consume_code_replay_rejected(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    repository.consume_code(admin_username="@admin", code=code, ttl_seconds=86400)
    with pytest.raises(InvalidLoginCode):
        repository.consume_code(
            admin_username="@admin", code=code, ttl_seconds=86400
        )


def test_consume_code_wrong_value_rejected(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    repository.request_code(admin_username="@admin", ttl_seconds=300)
    with pytest.raises(InvalidLoginCode):
        repository.consume_code(
            admin_username="@admin", code="000000", ttl_seconds=86400
        )


def test_consume_code_wrong_admin_rejected(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    with pytest.raises(InvalidLoginCode):
        repository.consume_code(
            admin_username="@other", code=code, ttl_seconds=86400
        )


def test_consume_code_expired_rejected(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    # Force expiry by rewriting expires_at in the past.
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE admin_login_codes SET expires_at = ? WHERE admin_username = ?",
            (past, "@admin"),
        )
    with pytest.raises(InvalidLoginCode):
        repository.consume_code(
            admin_username="@admin", code=code, ttl_seconds=86400
        )


def test_consume_code_no_pending_rejected(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    with pytest.raises(InvalidLoginCode):
        repository.consume_code(
            admin_username="@admin", code="123456", ttl_seconds=86400
        )


def test_validate_session_round_trip(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    session = repository.consume_code(
        admin_username="@admin", code=code, ttl_seconds=86400
    )
    found = repository.validate_session(session.token)
    assert found is not None
    assert found.admin_username == "@admin"


def test_validate_session_unknown_returns_none(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    assert repository.validate_session("nope") is None


def test_validate_session_expired_returns_none(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    session = repository.consume_code(
        admin_username="@admin", code=code, ttl_seconds=86400
    )
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE admin_sessions SET expires_at = ? WHERE admin_username = ?",
            (past, "@admin"),
        )
    assert repository.validate_session(session.token) is None


def test_revoke_session_invalidates(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    code = repository.request_code(admin_username="@admin", ttl_seconds=300)
    session = repository.consume_code(
        admin_username="@admin", code=code, ttl_seconds=86400
    )
    repository.revoke_session(session.token)
    assert repository.validate_session(session.token) is None


def test_revoke_session_missing_is_silent(tmp_path):
    repository = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    repository.revoke_session("does-not-exist")


def test_purge_expired_removes_only_expired_rows(tmp_path):
    path = str(tmp_path / "admin.sqlite3")
    repository = AdminAuthRepository(path)
    code_active = repository.request_code(admin_username="@a", ttl_seconds=300)
    code_to_expire = repository.request_code(admin_username="@b", ttl_seconds=300)
    session_active = repository.consume_code(
        admin_username="@a", code=code_active, ttl_seconds=86400
    )
    session_to_expire = repository.consume_code(
        admin_username="@b", code=code_to_expire, ttl_seconds=86400
    )
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE admin_sessions SET expires_at = ? WHERE admin_username = ?",
            (past, "@b"),
        )
    purged = repository.purge_expired()
    assert purged >= 1
    assert repository.validate_session(session_active.token) is not None
    assert repository.validate_session(session_to_expire.token) is None
