"""Bootstrap-only coverage for services_nl_op_sessions (Epic 12, story 12.01)."""

from __future__ import annotations

import sqlite3

from services.api.app.calendar.services_nl_op_session_repository import (
    init_services_nl_ops_schema,
)


def test_init_services_nl_ops_schema_creates_table_and_indexes(tmp_path):
    path = str(tmp_path / "nl_ops.sqlite3")
    init_services_nl_ops_schema(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(r["name"])
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "services_nl_op_sessions" in tables
        columns = {
            str(r["name"])
            for r in connection.execute(
                "PRAGMA table_info(services_nl_op_sessions)"
            )
        }
        assert columns == {
            "id",
            "project_id",
            "originating_operator",
            "op_type",
            "payload_json",
            "preview",
            "confirm_token_sha256",
            "status",
            "created_at",
            "expires_at",
            "consumed_at",
            "soft_deleted_at",
        }
        index_names = {
            str(r["name"])
            for r in connection.execute(
                "PRAGMA index_list(services_nl_op_sessions)"
            )
        }
        assert "services_nl_op_sessions_lookup_idx" in index_names
        assert "services_nl_op_sessions_expiry_idx" in index_names


def test_init_services_nl_ops_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "nl_ops.sqlite3")
    init_services_nl_ops_schema(path)
    # Second invocation must be a no-op (no errors, no schema drift).
    init_services_nl_ops_schema(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(r["name"])
            for r in connection.execute(
                "PRAGMA table_info(services_nl_op_sessions)"
            )
        }
    assert "id" in columns
