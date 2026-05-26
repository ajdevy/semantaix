"""Minimal `StateRepository` for SalesPersonaAnswerer (Story 12.03).

Story 12.01 owns the canonical sales DB schema (4 tables + indexes); this
module ships only what 12.03's answerer needs — the `sales_conversation_state`
table with idempotent bootstrap, `get`, and `upsert`. Later stories (and the
broader Story 12.01 PR) will add `transition_stage`, `mark_*`, `list_active`,
plus the other three repositories. Keeping the surface tight here makes 12.03
mergeable in isolation.

Sync ``sqlite3`` per the project-context rule; callers dispatch via
``asyncio.to_thread`` from the async answerer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _format_now(now: datetime) -> str:
    """Serialise an aware datetime to ISO-8601 UTC."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    return now.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_conversation_state (
                chat_id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                current_stage TEXT NOT NULL DEFAULT 'new',
                collected_intent_json TEXT NOT NULL DEFAULT '{}',
                last_proposal_json TEXT,
                last_customer_msg_at TEXT,
                last_bot_msg_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )


class StateRepository:
    def __init__(self, *, db_path: str) -> None:
        self.db_path = db_path
        init_schema(self.db_path)

    def get(self, chat_id: int) -> dict[str, Any] | None:
        """Return the state row as a dict (or ``None`` when absent).

        The returned shape matches the kwargs `upsert` accepts so
        callers can round-trip without bespoke marshalling.
        """
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT chat_id, project_id, current_stage,
                       collected_intent_json, last_proposal_json,
                       last_customer_msg_at, last_bot_msg_at
                FROM sales_conversation_state
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        if row is None:
            return None
        collected = json.loads(row["collected_intent_json"]) if row["collected_intent_json"] else {}
        proposal = (
            json.loads(row["last_proposal_json"])
            if row["last_proposal_json"]
            else None
        )
        return {
            "chat_id": int(row["chat_id"]),
            "project_id": int(row["project_id"]),
            "current_stage": str(row["current_stage"]),
            "collected_intent": collected,
            "last_proposal": proposal,
            "last_customer_msg_at": _parse_iso(row["last_customer_msg_at"]),
            "last_bot_msg_at": _parse_iso(row["last_bot_msg_at"]),
        }

    def upsert(
        self,
        *,
        chat_id: int,
        project_id: int,
        current_stage: str,
        collected_intent: dict[str, Any],
        now: datetime,
        last_proposal: dict[str, Any] | None = None,
        last_customer_msg_at: datetime | None = None,
        last_bot_msg_at: datetime | None = None,
    ) -> None:
        intent_json = json.dumps(
            collected_intent, ensure_ascii=False, sort_keys=True
        )
        proposal_json = (
            json.dumps(last_proposal, ensure_ascii=False, sort_keys=True)
            if last_proposal is not None
            else None
        )
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO sales_conversation_state (
                    chat_id,
                    project_id,
                    current_stage,
                    collected_intent_json,
                    last_proposal_json,
                    last_customer_msg_at,
                    last_bot_msg_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    current_stage = excluded.current_stage,
                    collected_intent_json = excluded.collected_intent_json,
                    last_proposal_json = excluded.last_proposal_json,
                    last_customer_msg_at = COALESCE(
                        excluded.last_customer_msg_at,
                        sales_conversation_state.last_customer_msg_at
                    ),
                    last_bot_msg_at = COALESCE(
                        excluded.last_bot_msg_at,
                        sales_conversation_state.last_bot_msg_at
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    int(chat_id),
                    int(project_id),
                    current_stage,
                    intent_json,
                    proposal_json,
                    _format_now(last_customer_msg_at) if last_customer_msg_at else None,
                    _format_now(last_bot_msg_at) if last_bot_msg_at else None,
                    _format_now(now),
                ),
            )
