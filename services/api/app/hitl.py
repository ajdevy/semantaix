from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hitl_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_ref TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                operator_username TEXT,
                target_chat_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(hitl_tickets)").fetchall()
        ]
        if "target_chat_id" not in columns:
            connection.execute("ALTER TABLE hitl_tickets ADD COLUMN target_chat_id INTEGER")


@dataclass(frozen=True)
class HitlTicket:
    id: int
    conversation_ref: str
    reason: str
    status: str
    operator_username: str | None
    target_chat_id: int | None
    created_at: str
    updated_at: str
    resolved_at: str | None


class HitlTicketRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def create(
        self,
        *,
        conversation_ref: str,
        reason: str,
        target_chat_id: int | None = None,
    ) -> HitlTicket:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO hitl_tickets (
                    conversation_ref, reason, status, operator_username,
                    target_chat_id, created_at, updated_at, resolved_at
                )
                VALUES (?, ?, 'open', NULL, ?, ?, ?, NULL)
                """,
                (conversation_ref, reason, target_chat_id, now, now),
            )
            row = connection.execute(
                """
                SELECT id, conversation_ref, reason, status, operator_username, target_chat_id,
                       created_at, updated_at, resolved_at
                FROM hitl_tickets
                WHERE id = ?
                """,
                (int(cursor.lastrowid),),
            ).fetchone()
            assert row is not None
            return self._row_to_ticket(row)

    def assign(self, *, ticket_id: int, operator_username: str) -> HitlTicket:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE hitl_tickets
                SET operator_username = ?, status = 'assigned', updated_at = ?
                WHERE id = ?
                """,
                (operator_username, _now(), ticket_id),
            )
            row = connection.execute(
                """
                SELECT id, conversation_ref, reason, status, operator_username, target_chat_id,
                       created_at, updated_at, resolved_at
                FROM hitl_tickets
                WHERE id = ?
                """,
                (ticket_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_ticket(row)

    def resolve(self, *, ticket_id: int) -> HitlTicket:
        init_schema(self.db_path)
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE hitl_tickets
                SET status = 'resolved', resolved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, ticket_id),
            )
            row = connection.execute(
                """
                SELECT id, conversation_ref, reason, status, operator_username, target_chat_id,
                       created_at, updated_at, resolved_at
                FROM hitl_tickets
                WHERE id = ?
                """,
                (ticket_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_ticket(row)

    def list_all(self) -> list[HitlTicket]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_ref, reason, status, operator_username, target_chat_id,
                       created_at, updated_at, resolved_at
                FROM hitl_tickets
                ORDER BY id DESC
                """
            ).fetchall()
            return [self._row_to_ticket(row) for row in rows]

    def get(self, ticket_id: int) -> HitlTicket:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, conversation_ref, reason, status, operator_username, target_chat_id,
                       created_at, updated_at, resolved_at
                FROM hitl_tickets
                WHERE id = ?
                """,
                (ticket_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_ticket(row)

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> HitlTicket:
        return HitlTicket(
            id=int(row["id"]),
            conversation_ref=str(row["conversation_ref"]),
            reason=str(row["reason"]),
            status=str(row["status"]),
            operator_username=str(row["operator_username"]) if row["operator_username"] else None,
            target_chat_id=(
                int(row["target_chat_id"]) if row["target_chat_id"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
        )
