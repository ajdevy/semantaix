from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_PROJECT_SLUG = "default"
DEFAULT_PROJECT_NAME = "Default"


class ProjectSlugConflict(Exception):
    """Raised when creating a project with an existing slug."""


class ProjectReferenced(Exception):
    """Raised when deleting a project that is still referenced elsewhere."""


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Project:
    id: int
    slug: str
    name: str
    description: str | None
    created_at: str
    updated_at: str


class ProjectRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug)"
            )

    def create(self, *, slug: str, name: str, description: str | None = None) -> Project:
        now = _now()
        try:
            with _connect(self.db_path) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO projects (slug, name, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (slug, name, description, now, now),
                )
                row_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ProjectSlugConflict(slug) from exc
        fetched = self.get(row_id)
        assert fetched is not None
        return fetched

    def get_by_slug(self, slug: str) -> Project | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT id, slug, name, description, created_at, updated_at "
                "FROM projects WHERE slug = ?",
                (slug,),
            ).fetchone()
        return _row_to_project(row) if row is not None else None

    def get(self, project_id: int) -> Project | None:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT id, slug, name, description, created_at, updated_at "
                "FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        return _row_to_project(row) if row is not None else None

    def list_all(self) -> list[Project]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT id, slug, name, description, created_at, updated_at "
                "FROM projects ORDER BY id ASC"
            ).fetchall()
        return [_row_to_project(row) for row in rows]

    def update(
        self,
        *,
        slug: str,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        existing = self.get_by_slug(slug)
        if existing is None:
            raise LookupError(slug)
        new_name = name if name is not None else existing.name
        new_description = description if description is not None else existing.description
        now = _now()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, updated_at = ?
                WHERE slug = ?
                """,
                (new_name, new_description, now, slug),
            )
        updated = self.get_by_slug(slug)
        assert updated is not None
        return updated

    def delete(
        self,
        slug: str,
        *,
        is_referenced: Callable[[int], bool] | None = None,
    ) -> None:
        existing = self.get_by_slug(slug)
        if existing is None:
            raise LookupError(slug)
        if is_referenced is not None and is_referenced(existing.id):
            raise ProjectReferenced(slug)
        with _connect(self.db_path) as connection:
            connection.execute("DELETE FROM projects WHERE slug = ?", (slug,))

    def ensure_default_project(self) -> Project:
        existing = self.get_by_slug(DEFAULT_PROJECT_SLUG)
        if existing is not None:
            return existing
        return self.create(slug=DEFAULT_PROJECT_SLUG, name=DEFAULT_PROJECT_NAME)


def _row_to_project(row: sqlite3.Row) -> Project:
    description = row["description"]
    return Project(
        id=int(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        description=str(description) if description is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
