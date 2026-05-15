"""Persistent registry of operator file uploads.

Every file the operator sends via the KB-upload flow gets a row here — even
when KB ingestion is skipped (file too large, unsupported type, download
failed). The Telegram `file_id` is preserved so the operator can later resend
the file to a customer via `/send`, which uses `sendDocument` — that endpoint
has no 20 MB cap, so even files that failed `getFile` are still re-sendable.

`short_id` is an 8-character base32 alias the operator types in `/send`; the
opaque Telegram `file_id` stays internal.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.bot_gateway.app.telegram_update import TelegramAttachment

_SHORT_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SHORT_ID_LENGTH = 8
_INSERT_MAX_RETRIES = 25


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _generate_short_id() -> str:
    return "".join(
        secrets.choice(_SHORT_ID_ALPHABET) for _ in range(_SHORT_ID_LENGTH)
    )


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_files (
                short_id TEXT PRIMARY KEY,
                telegram_file_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                source_message_id INTEGER NOT NULL,
                source_file_name TEXT,
                source_file_type TEXT,
                mime_type TEXT,
                file_size_bytes INTEGER,
                is_confidential INTEGER NOT NULL,
                stored_binary_path TEXT,
                download_status TEXT NOT NULL,
                kb_ingest_status TEXT NOT NULL,
                kb_inserted_chunks INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS operator_files_username_created_idx
                ON operator_files (username, created_at DESC)
            """
        )
        columns = {
            str(r["name"])
            for r in connection.execute(
                "PRAGMA table_info(operator_files)"
            ).fetchall()
        }
        if "project_id" not in columns:
            connection.execute(
                "ALTER TABLE operator_files ADD COLUMN project_id INTEGER"
            )


@dataclass(frozen=True)
class OperatorFileRecord:
    short_id: str
    telegram_file_id: str
    chat_id: int
    username: str
    source_message_id: int
    source_file_name: str | None
    source_file_type: str | None
    mime_type: str | None
    file_size_bytes: int | None
    is_confidential: bool
    stored_binary_path: str | None
    download_status: str
    kb_ingest_status: str
    kb_inserted_chunks: int | None
    created_at: str
    updated_at: str


class OperatorFileRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def record_upload(
        self,
        *,
        chat_id: int,
        username: str,
        source_message_id: int,
        attachment: TelegramAttachment,
        is_confidential: bool,
        stored_binary_path: str | None,
        download_status: str,
        source_file_type: str | None,
        kb_ingest_status: str = "pending",
        kb_inserted_chunks: int | None = None,
    ) -> OperatorFileRecord:
        created_at = _now_iso()
        updated_at = created_at
        last_error: Exception | None = None
        for _ in range(_INSERT_MAX_RETRIES):
            short_id = _generate_short_id()
            try:
                with _connect(self.db_path) as connection:
                    connection.execute(
                        """
                        INSERT INTO operator_files (
                            short_id, telegram_file_id, chat_id, username,
                            source_message_id, source_file_name,
                            source_file_type, mime_type, file_size_bytes,
                            is_confidential, stored_binary_path,
                            download_status, kb_ingest_status,
                            kb_inserted_chunks, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            short_id,
                            attachment.file_id,
                            chat_id,
                            username,
                            source_message_id,
                            attachment.file_name,
                            source_file_type,
                            attachment.mime_type,
                            attachment.file_size,
                            1 if is_confidential else 0,
                            stored_binary_path,
                            download_status,
                            kb_ingest_status,
                            kb_inserted_chunks,
                            created_at,
                            updated_at,
                        ),
                    )
                return OperatorFileRecord(
                    short_id=short_id,
                    telegram_file_id=attachment.file_id,
                    chat_id=chat_id,
                    username=username,
                    source_message_id=source_message_id,
                    source_file_name=attachment.file_name,
                    source_file_type=source_file_type,
                    mime_type=attachment.mime_type,
                    file_size_bytes=attachment.file_size,
                    is_confidential=is_confidential,
                    stored_binary_path=stored_binary_path,
                    download_status=download_status,
                    kb_ingest_status=kb_ingest_status,
                    kb_inserted_chunks=kb_inserted_chunks,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            except sqlite3.IntegrityError as exc:
                last_error = exc
                continue
        raise RuntimeError(
            "operator_files: exhausted short_id collision retries"
        ) from last_error

    def update_kb_status(
        self,
        *,
        short_id: str,
        kb_ingest_status: str,
        kb_inserted_chunks: int | None,
    ) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE operator_files
                SET kb_ingest_status = ?,
                    kb_inserted_chunks = ?,
                    updated_at = ?
                WHERE short_id = ?
                """,
                (kb_ingest_status, kb_inserted_chunks, _now_iso(), short_id),
            )

    def list_recent(
        self, *, username: str, limit: int
    ) -> list[OperatorFileRecord]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM operator_files
                WHERE username = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (username, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def get(self, *, short_id: str) -> OperatorFileRecord | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM operator_files WHERE short_id = ?",
                (short_id,),
            ).fetchone()
        return _row_to_record(row) if row is not None else None


def _row_to_record(row: sqlite3.Row) -> OperatorFileRecord:
    return OperatorFileRecord(
        short_id=str(row["short_id"]),
        telegram_file_id=str(row["telegram_file_id"]),
        chat_id=int(row["chat_id"]),
        username=str(row["username"]),
        source_message_id=int(row["source_message_id"]),
        source_file_name=(
            str(row["source_file_name"])
            if row["source_file_name"] is not None
            else None
        ),
        source_file_type=(
            str(row["source_file_type"])
            if row["source_file_type"] is not None
            else None
        ),
        mime_type=(
            str(row["mime_type"]) if row["mime_type"] is not None else None
        ),
        file_size_bytes=(
            int(row["file_size_bytes"])
            if row["file_size_bytes"] is not None
            else None
        ),
        is_confidential=bool(row["is_confidential"]),
        stored_binary_path=(
            str(row["stored_binary_path"])
            if row["stored_binary_path"] is not None
            else None
        ),
        download_status=str(row["download_status"]),
        kb_ingest_status=str(row["kb_ingest_status"]),
        kb_inserted_chunks=(
            int(row["kb_inserted_chunks"])
            if row["kb_inserted_chunks"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
