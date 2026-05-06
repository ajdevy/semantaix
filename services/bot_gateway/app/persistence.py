from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from platform_common.settings import get_settings


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
            """
        )


class TelegramConversationRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def create_or_get_conversation(self, telegram_user_id: int) -> int:
        now = _timestamp()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO conversations (telegram_user_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (telegram_user_id, now, now),
            )
            row = connection.execute(
                "SELECT id FROM conversations WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            assert row is not None
            return int(row["id"])

    def append_message_if_new(
        self,
        *,
        conversation_id: int,
        source_message_id: int,
        role: str,
        text: str,
        trace_id: str,
    ) -> bool:
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    conversation_id, source_message_id, role, text, trace_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, source_message_id, role, text, trace_id, _timestamp()),
            )
            return cursor.rowcount > 0


def persist_normalized_message(
    *,
    telegram_user_id: int,
    source_message_id: int,
    text: str,
    trace_id: str,
) -> bool:
    settings = get_settings()
    repository = TelegramConversationRepository(settings.persistence_db_path)
    conversation_id = repository.create_or_get_conversation(telegram_user_id)
    return repository.append_message_if_new(
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        role="user",
        text=text,
        trace_id=trace_id,
    )
