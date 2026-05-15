import sqlite3

from services.api.app.admin_auth import AdminAuthRepository


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
