"""Reusable sqlite seed data and shared fixtures for E2E scenarios."""

import json
import sqlite3
from pathlib import Path

TELEGRAM_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "telegram"


def load_telegram_fixture(name: str) -> dict:
    """Read a Telegram update fixture from tests/fixtures/telegram/."""
    return json.loads((TELEGRAM_FIXTURE_DIR / name).read_text(encoding="utf-8"))


DEFAULT_TRANSCRIPT_MESSAGES: list[dict[str, object]] = [
    {
        "conversation_id": 1,
        "source_message_id": 100,
        "role": "user",
        "text": "Hello",
        "trace_id": "t1",
        "created_at": "2026-01-01T00:00:00Z",
    },
    {
        "conversation_id": 1,
        "source_message_id": 101,
        "role": "user",
        "text": "Reset password via settings and email token.",
        "trace_id": "t2",
        "created_at": "2026-01-01T00:00:01Z",
    },
    {
        "conversation_id": 2,
        "source_message_id": 200,
        "role": "user",
        "text": "Billing cycle is monthly with invoice on day one.",
        "trace_id": "t3",
        "created_at": "2026-01-01T00:00:02Z",
    },
]


def seed_transcripts(path: str, messages: list[dict[str, object]]) -> None:
    """Create a persistence-style messages table and insert the given rows."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, role, text, trace_id, created_at
            )
            VALUES (
                :conversation_id, :source_message_id, :role, :text, :trace_id, :created_at
            )
            """,
            messages,
        )


def seed_transcript_messages(path: str) -> None:
    """Seed the default transcript fixture used by /knowledge/extract tests."""
    seed_transcripts(path, DEFAULT_TRANSCRIPT_MESSAGES)
