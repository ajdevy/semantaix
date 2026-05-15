import sqlite3

from services.api.app.admin_nl_ops import AdminNlOpsRepository


def test_init_schema_creates_table(tmp_path):
    path = str(tmp_path / "nl_ops.sqlite3")
    AdminNlOpsRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {row[0] for row in rows}
    assert "admin_nl_op_sessions" in names


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "nl_ops.sqlite3")
    repository = AdminNlOpsRepository(path)
    repository.init_schema()
    repository.init_schema()
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(admin_nl_op_sessions)"
        ).fetchall()
    columns = {row[1] for row in rows}
    assert {
        "id",
        "admin_username",
        "utterance",
        "op_type",
        "payload_json",
        "status",
        "confirm_token",
        "created_at",
        "updated_at",
    }.issubset(columns)
