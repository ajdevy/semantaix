"""``client_materials`` table + repository (Story 12.05b minimal surface).

Story 12.01 owns the canonical ``client_materials`` schema (kind, local_path,
byte_size, duration_seconds, caption, tags_json, source_operator_file_id,
telegram_file_id, is_active). 12.05b only needs the ``add(...)`` writer — the
analyzer registers one row per KB-promoted file. The schema matches 12.01 so
the table is forward-compatible when the full repo (list/get/pick/update/
soft_delete) lands later.

All public methods are keyword-only; the ``now`` datetime is injected for
deterministic tests and stored verbatim. JSON columns use the
``ensure_ascii=False, sort_keys=True`` discipline shared with the other sales
repos so diffs stay stable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("now must be tz-aware")
    return value.astimezone(UTC).isoformat()


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS client_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                telegram_file_id TEXT,
                local_path TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                duration_seconds INTEGER,
                caption TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                source_operator_file_id TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_client_materials_project
                ON client_materials (project_id, is_active)
            """
        )


class ClientMaterialsRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        init_schema(self.db_path)

    def add(
        self,
        *,
        project_id: int,
        kind: str,
        local_path: str,
        byte_size: int,
        now: datetime,
        duration_seconds: int | None = None,
        caption: str | None = None,
        tags: list[str] | None = None,
        telegram_file_id: str | None = None,
        source_operator_file_id: str | None = None,
    ) -> int:
        timestamp = _format_utc(now)
        tags_payload = json.dumps(
            list(tags) if tags is not None else [],
            ensure_ascii=False,
            sort_keys=True,
        )
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO client_materials (
                    project_id,
                    kind,
                    telegram_file_id,
                    local_path,
                    byte_size,
                    duration_seconds,
                    caption,
                    tags_json,
                    source_operator_file_id,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(project_id),
                    kind,
                    telegram_file_id,
                    local_path,
                    int(byte_size),
                    duration_seconds,
                    caption,
                    tags_payload,
                    source_operator_file_id,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)
