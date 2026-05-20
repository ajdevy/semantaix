"""Per-project LLM prompt configuration.

Holds editable copies of the prompts and guardrail lists that shape the
LLM grounding pipeline. A project that has no override falls back to the
hardcoded defaults shipped with the code so behavior is unchanged for
installations that never touch the UI or bot commands.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from services.api.app import openrouter_client

PROMPT_NAME_LIST: tuple[str, ...] = (
    "grounding_system",
    "verifier_system",
    "inbound_ack",
    "guardrail_hedges",
    "guardrail_policy",
    "guardrail_profanity",
)
PROMPT_NAMES: frozenset[str] = frozenset(PROMPT_NAME_LIST)

MAX_PROMPT_VALUE_BYTES = 16 * 1024  # 16 KiB cap per prompt value.
PENDING_EDIT_TTL_SECONDS = 600  # 10-minute window for the bot multi-step set.


class UnknownPromptName(Exception):
    """Raised when a caller passes a prompt_name outside ``PROMPT_NAMES``."""


class PromptValueTooLarge(Exception):
    """Raised when a value exceeds ``MAX_PROMPT_VALUE_BYTES``."""


class PromptValueInvalid(Exception):
    """Raised when a value fails per-name validation (empty, missing
    required placeholders, etc.)."""


class PromptVersionNotFound(Exception):
    """Raised when restoring or fetching a non-existent version."""


_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"
_GUARDRAIL_FILES: dict[str, str] = {
    "guardrail_hedges": "russian_hedges.txt",
    "guardrail_policy": "russian_policy_phrases.txt",
    "guardrail_profanity": "russian_profanity.txt",
}


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


@dataclass(frozen=True)
class PromptCurrent:
    project_id: int
    prompt_name: str
    value: str
    version: int
    updated_at: str
    updated_by: str


@dataclass(frozen=True)
class PromptVersion:
    version: int
    value: str
    edited_by: str
    created_at: str


@dataclass(frozen=True)
class PendingEdit:
    user_username: str
    project_id: int
    prompt_name: str
    created_at: str
    expires_at: str


def _ensure_known_name(prompt_name: str) -> None:
    if prompt_name not in PROMPT_NAMES:
        raise UnknownPromptName(prompt_name)


@lru_cache(maxsize=4)
def _read_data_file(name: str) -> str:
    return (_DATA_ROOT / name).read_text(encoding="utf-8")


def default_prompt(prompt_name: str) -> str:
    """Return the hardcoded fallback for ``prompt_name``."""
    _ensure_known_name(prompt_name)
    if prompt_name == "grounding_system":
        return openrouter_client._GROUNDING_SYSTEM_PROMPT_TEMPLATE
    if prompt_name == "verifier_system":
        return openrouter_client._VERIFIER_SYSTEM_PROMPT
    if prompt_name == "inbound_ack":
        from platform_common.settings import get_settings

        return get_settings().inbound_ack_message
    return _read_data_file(_GUARDRAIL_FILES[prompt_name])


def validate_value(prompt_name: str, value: str) -> None:
    """Raise on invalid values; return silently when ok."""
    _ensure_known_name(prompt_name)
    if not value:
        raise PromptValueInvalid(f"{prompt_name}: empty value")
    if len(value.encode("utf-8")) > MAX_PROMPT_VALUE_BYTES:
        raise PromptValueTooLarge(prompt_name)
    if prompt_name == "grounding_system":
        if "{name}" not in value or "{today_iso}" not in value:
            raise PromptValueInvalid(
                "grounding_system: must contain {name} and {today_iso}"
            )


def normalize_value(prompt_name: str, value: str) -> str:
    """Canonicalize a value for storage.

    Guardrail lists are stored as newline-separated entries with leading
    and trailing whitespace stripped and blank lines dropped. Other
    prompts are stored verbatim.
    """
    _ensure_known_name(prompt_name)
    if prompt_name.startswith("guardrail_"):
        lines = [line.strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)
    return value


def split_guardrail_lines(value: str) -> list[str]:
    """Return non-empty, stripped lines from a guardrail prompt value."""
    return [line.strip() for line in value.splitlines() if line.strip()]


class ProjectPromptRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.init_schema()

    def init_schema(self) -> None:
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_prompts (
                    project_id INTEGER NOT NULL,
                    prompt_name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    PRIMARY KEY (project_id, prompt_name)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_project_prompts_project "
                "ON project_prompts(project_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_prompt_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    prompt_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    value TEXT NOT NULL,
                    edited_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, prompt_name, version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_pp_versions_lookup "
                "ON project_prompt_versions(project_id, prompt_name, version DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_prompt_edits (
                    user_username TEXT PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    prompt_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )

    def get(self, *, project_id: int, prompt_name: str) -> str | None:
        _ensure_known_name(prompt_name)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT value FROM project_prompts "
                "WHERE project_id = ? AND prompt_name = ?",
                (project_id, prompt_name),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def get_current(
        self, *, project_id: int, prompt_name: str
    ) -> PromptCurrent | None:
        _ensure_known_name(prompt_name)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT project_id, prompt_name, value, version, "
                "updated_at, updated_by FROM project_prompts "
                "WHERE project_id = ? AND prompt_name = ?",
                (project_id, prompt_name),
            ).fetchone()
        if row is None:
            return None
        return _row_to_current(row)

    def list_current(self, project_id: int) -> list[PromptCurrent]:
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT project_id, prompt_name, value, version, "
                "updated_at, updated_by FROM project_prompts "
                "WHERE project_id = ? ORDER BY prompt_name ASC",
                (project_id,),
            ).fetchall()
        return [_row_to_current(row) for row in rows]

    def set(
        self,
        *,
        project_id: int,
        prompt_name: str,
        value: str,
        edited_by: str,
    ) -> int:
        _ensure_known_name(prompt_name)
        normalized = normalize_value(prompt_name, value)
        validate_value(prompt_name, normalized)
        now = _now_iso()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT version FROM project_prompts "
                "WHERE project_id = ? AND prompt_name = ?",
                (project_id, prompt_name),
            ).fetchone()
            new_version = (int(row["version"]) + 1) if row is not None else 1
            connection.execute(
                """
                INSERT INTO project_prompts (
                    project_id, prompt_name, value, version,
                    updated_at, updated_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, prompt_name) DO UPDATE SET
                    value = excluded.value,
                    version = excluded.version,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (
                    project_id,
                    prompt_name,
                    normalized,
                    new_version,
                    now,
                    edited_by,
                ),
            )
            connection.execute(
                """
                INSERT INTO project_prompt_versions (
                    project_id, prompt_name, version, value,
                    edited_by, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    prompt_name,
                    new_version,
                    normalized,
                    edited_by,
                    now,
                ),
            )
        return new_version

    def list_versions(
        self, *, project_id: int, prompt_name: str, limit: int = 50
    ) -> list[PromptVersion]:
        _ensure_known_name(prompt_name)
        with _connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT version, value, edited_by, created_at
                FROM project_prompt_versions
                WHERE project_id = ? AND prompt_name = ?
                ORDER BY version DESC
                LIMIT ?
                """,
                (project_id, prompt_name, limit),
            ).fetchall()
        return [_row_to_version(row) for row in rows]

    def get_version(
        self, *, project_id: int, prompt_name: str, version: int
    ) -> PromptVersion | None:
        _ensure_known_name(prompt_name)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT version, value, edited_by, created_at
                FROM project_prompt_versions
                WHERE project_id = ? AND prompt_name = ? AND version = ?
                """,
                (project_id, prompt_name, version),
            ).fetchone()
        if row is None:
            return None
        return _row_to_version(row)

    def restore(
        self,
        *,
        project_id: int,
        prompt_name: str,
        version: int,
        edited_by: str,
    ) -> int:
        target = self.get_version(
            project_id=project_id, prompt_name=prompt_name, version=version
        )
        if target is None:
            raise PromptVersionNotFound(
                f"{prompt_name} v{version} for project {project_id}"
            )
        return self.set(
            project_id=project_id,
            prompt_name=prompt_name,
            value=target.value,
            edited_by=edited_by,
        )

    def arm_pending(
        self, *, user_username: str, project_id: int, prompt_name: str
    ) -> PendingEdit:
        _ensure_known_name(prompt_name)
        normalized_user = _normalize_username(user_username)
        now = _now()
        expires_at = now + timedelta(seconds=PENDING_EDIT_TTL_SECONDS)
        created_iso = now.isoformat()
        expires_iso = expires_at.isoformat()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO pending_prompt_edits (
                    user_username, project_id, prompt_name,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_username) DO UPDATE SET
                    project_id = excluded.project_id,
                    prompt_name = excluded.prompt_name,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    normalized_user,
                    project_id,
                    prompt_name,
                    created_iso,
                    expires_iso,
                ),
            )
        return PendingEdit(
            user_username=normalized_user,
            project_id=project_id,
            prompt_name=prompt_name,
            created_at=created_iso,
            expires_at=expires_iso,
        )

    def peek_pending(self, user_username: str) -> PendingEdit | None:
        normalized_user = _normalize_username(user_username)
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT user_username, project_id, prompt_name,
                       created_at, expires_at
                FROM pending_prompt_edits
                WHERE user_username = ?
                """,
                (normalized_user,),
            ).fetchone()
        if row is None:
            return None
        pending = PendingEdit(
            user_username=str(row["user_username"]),
            project_id=int(row["project_id"]),
            prompt_name=str(row["prompt_name"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )
        if pending.expires_at <= _now_iso():
            self.cancel_pending(user_username=normalized_user)
            return None
        return pending

    def consume_pending(self, user_username: str) -> PendingEdit | None:
        pending = self.peek_pending(user_username)
        if pending is None:
            return None
        self.cancel_pending(user_username=pending.user_username)
        return pending

    def cancel_pending(self, *, user_username: str) -> bool:
        normalized_user = _normalize_username(user_username)
        with _connect(self.db_path) as connection:
            cursor = connection.execute(
                "DELETE FROM pending_prompt_edits WHERE user_username = ?",
                (normalized_user,),
            )
        return cursor.rowcount > 0


def _normalize_username(username: str) -> str:
    return username.strip().lower().lstrip("@")


def _row_to_current(row: sqlite3.Row) -> PromptCurrent:
    return PromptCurrent(
        project_id=int(row["project_id"]),
        prompt_name=str(row["prompt_name"]),
        value=str(row["value"]),
        version=int(row["version"]),
        updated_at=str(row["updated_at"]),
        updated_by=str(row["updated_by"]),
    )


def _row_to_version(row: sqlite3.Row) -> PromptVersion:
    return PromptVersion(
        version=int(row["version"]),
        value=str(row["value"]),
        edited_by=str(row["edited_by"]),
        created_at=str(row["created_at"]),
    )


def resolve_prompt(
    repo: ProjectPromptRepository,
    project_id: int | None,
    prompt_name: str,
) -> str:
    """Return the project's override if present, else the hardcoded default."""
    if project_id is not None:
        override = repo.get(project_id=project_id, prompt_name=prompt_name)
        if override is not None:
            return override
    return default_prompt(prompt_name)
