from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class OperatorUsernameConflict(Exception):
    """Raised when creating an operator with an already-registered username."""


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Operator:
    id: int
    username: str
    chat_id: int | None
    project_id: int
    display_name: str | None
    is_active: bool
    created_at: str
    updated_at: str


class OperatorRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    chat_id INTEGER,
                    project_id INTEGER NOT NULL,
                    display_name TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_operators_username "
                "ON operators(username)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_operators_project "
                "ON operators(project_id)"
            )

    def create(
        self,
        *,
        username: str,
        project_id: int,
        chat_id: int | None = None,
        display_name: str | None = None,
    ) -> Operator:
        now = _now()
        try:
            with _connect(self.db_path) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO operators
                        (username, chat_id, project_id, display_name,
                         is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (username, chat_id, project_id, display_name, now, now),
                )
                row_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise OperatorUsernameConflict(username) from exc
        fetched = self._get_by_id(row_id)
        assert fetched is not None
        return fetched

    def find_by_username(self, username: str) -> Operator | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                _SELECT_SQL + " WHERE username = ?",
                (username,),
            ).fetchone()
        return _row_to_operator(row) if row is not None else None

    def list_active(self) -> list[Operator]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                _SELECT_SQL + " WHERE is_active = 1 ORDER BY id ASC"
            ).fetchall()
        return [_row_to_operator(row) for row in rows]

    def list_all(self) -> list[Operator]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(_SELECT_SQL + " ORDER BY id ASC").fetchall()
        return [_row_to_operator(row) for row in rows]

    def list_by_project_id(self, project_id: int) -> list[Operator]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                _SELECT_SQL + " WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            ).fetchall()
        return [_row_to_operator(row) for row in rows]

    def update(
        self,
        *,
        username: str,
        project_id: int | None = None,
        chat_id: int | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
    ) -> Operator:
        existing = self.find_by_username(username)
        if existing is None:
            raise LookupError(username)
        new_project_id = (
            project_id if project_id is not None else existing.project_id
        )
        new_chat_id = chat_id if chat_id is not None else existing.chat_id
        new_display_name = (
            display_name if display_name is not None else existing.display_name
        )
        new_is_active = is_active if is_active is not None else existing.is_active
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE operators
                SET project_id = ?,
                    chat_id = ?,
                    display_name = ?,
                    is_active = ?,
                    updated_at = ?
                WHERE username = ?
                """,
                (
                    new_project_id,
                    new_chat_id,
                    new_display_name,
                    1 if new_is_active else 0,
                    now,
                    username,
                ),
            )
        updated = self.find_by_username(username)
        assert updated is not None
        return updated

    def ensure_default_operator(
        self,
        *,
        username: str,
        project_id: int,
        chat_id: int | None = None,
    ) -> Operator:
        existing = self.find_by_username(username)
        if existing is None:
            return self.create(
                username=username, project_id=project_id, chat_id=chat_id
            )
        if chat_id is not None and chat_id != existing.chat_id:
            return self.update(username=username, chat_id=chat_id)
        return existing

    def any_referencing_project(self, project_id: int) -> bool:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM operators WHERE project_id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
        return row is not None

    def _get_by_id(self, operator_id: int) -> Operator | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                _SELECT_SQL + " WHERE id = ?",
                (operator_id,),
            ).fetchone()
        return _row_to_operator(row) if row is not None else None


_SELECT_SQL = (
    "SELECT id, username, chat_id, project_id, display_name, is_active, "
    "created_at, updated_at FROM operators"
)


def _row_to_operator(row: sqlite3.Row) -> Operator:
    chat_id_raw = row["chat_id"]
    display_raw = row["display_name"]
    return Operator(
        id=int(row["id"]),
        username=str(row["username"]),
        chat_id=int(chat_id_raw) if chat_id_raw is not None else None,
        project_id=int(row["project_id"]),
        display_name=str(display_raw) if display_raw is not None else None,
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
