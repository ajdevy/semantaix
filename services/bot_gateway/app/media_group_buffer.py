"""SQLite-backed buffer for Telegram media groups.

When the operator selects multiple files in Telegram's file picker and sends
them together, Telegram delivers each file as a separate webhook update that
shares one `media_group_id`. We buffer those attachments here so a single
debounced flush can ack the operator with "Принял N файлов" and run one
batch upload (instead of N independent acks and N summary DMs).

State lives in the same SQLite file as `operator_kb_session` (the hitl DB),
matching the existing pattern in `kb_session.py`. Persistence — rather than
an in-memory dict — means the buffer survives a bot_gateway restart that
overlaps the debounce window.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.bot_gateway.app.telegram_update import TelegramAttachment


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_media_group_buffer (
                media_group_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                update_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                attachment_json TEXT NOT NULL,
                is_confidential INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (media_group_id, update_id)
            )
            """
        )


@dataclass(frozen=True)
class BufferedAttachment:
    media_group_id: str
    chat_id: int
    username: str
    update_id: int
    source_message_id: int
    attachment: TelegramAttachment
    is_confidential: bool
    received_at: str


class MediaGroupBuffer:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def add(
        self,
        *,
        media_group_id: str,
        chat_id: int,
        username: str,
        update_id: int,
        source_message_id: int,
        attachment: TelegramAttachment,
        is_confidential: bool,
    ) -> bool:
        """Add an attachment to the group.

        Returns True iff this call inserted the FIRST row for the group —
        the caller uses that signal to schedule the debounced flush exactly
        once. Duplicate (media_group_id, update_id) inserts are ignored and
        return False (matches Telegram retry semantics).
        """
        payload_json = json.dumps(asdict(attachment))
        received_at = _now_iso()
        with _connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = connection.execute(
                "SELECT COUNT(*) FROM operator_media_group_buffer "
                "WHERE media_group_id = ?",
                (media_group_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT OR IGNORE INTO operator_media_group_buffer
                    (media_group_id, chat_id, username, update_id,
                     source_message_id, attachment_json, is_confidential,
                     received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_group_id,
                    chat_id,
                    username,
                    update_id,
                    source_message_id,
                    payload_json,
                    1 if is_confidential else 0,
                    received_at,
                ),
            )
            connection.execute("COMMIT")
        return before == 0

    def drain(self, *, media_group_id: str) -> list[BufferedAttachment]:
        """Atomically read + delete all attachments for a group.

        Returns [] if the group has already been drained (concurrent winner
        path). Order matches insertion order via the `update_id` column.
        """
        with _connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT media_group_id, chat_id, username, update_id,
                       source_message_id, attachment_json, is_confidential,
                       received_at
                FROM operator_media_group_buffer
                WHERE media_group_id = ?
                ORDER BY update_id ASC
                """,
                (media_group_id,),
            ).fetchall()
            if rows:
                connection.execute(
                    "DELETE FROM operator_media_group_buffer "
                    "WHERE media_group_id = ?",
                    (media_group_id,),
                )
            connection.execute("COMMIT")
        return [_row_to_buffered(row) for row in rows]


def _row_to_buffered(row: sqlite3.Row) -> BufferedAttachment:
    payload = json.loads(row["attachment_json"])
    attachment = TelegramAttachment(
        file_id=payload["file_id"],
        kind=payload["kind"],
        mime_type=payload.get("mime_type"),
        file_size=payload.get("file_size"),
        file_name=payload.get("file_name"),
    )
    return BufferedAttachment(
        media_group_id=str(row["media_group_id"]),
        chat_id=int(row["chat_id"]),
        username=str(row["username"]),
        update_id=int(row["update_id"]),
        source_message_id=int(row["source_message_id"]),
        attachment=attachment,
        is_confidential=bool(row["is_confidential"]),
        received_at=str(row["received_at"]),
    )
