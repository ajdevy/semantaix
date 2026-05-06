"""Reusable sqlite seed data for E2E scenarios."""

import sqlite3


def seed_transcript_messages(path: str) -> None:
    """Minimal persistence-style DB for /knowledge/extract."""
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
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, role, text, trace_id, created_at
            )
            VALUES
                (1, 100, 'user', 'Hello', 't1', '2026-01-01T00:00:00Z'),
                (
                    1, 101, 'user', 'Reset password via settings and email token.',
                    't2', '2026-01-01T00:00:01Z'
                ),
                (
                    2, 200, 'user', 'Billing cycle is monthly with invoice on day one.',
                    't3', '2026-01-01T00:00:02Z'
                )
            """
        )
