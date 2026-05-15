"""Admin natural-language operations repository.

Story 10.01 lands the schema only. The propose/confirm/cancel workflow and
intent dispatch land in story 10.05.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class AdminNlOpsRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_nl_op_sessions (
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_nl_ops_admin "
                "ON admin_nl_op_sessions(admin_username)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_admin_nl_ops_status "
                "ON admin_nl_op_sessions(status)"
            )
