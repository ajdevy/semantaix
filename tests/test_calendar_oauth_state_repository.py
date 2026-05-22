import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app.calendar.oauth_state_repository import (
    CalendarOAuthStateRepository,
    InvalidOAuthState,
    PendingState,
)


def _repo(tmp_path) -> CalendarOAuthStateRepository:
    return CalendarOAuthStateRepository(db_path=str(tmp_path / "calendar.sqlite3"))


def test_init_schema_creates_table(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    CalendarOAuthStateRepository(db_path=path)
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "calendar_oauth_pending_state" in names


def test_create_stores_hash_and_ttl(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    repo = CalendarOAuthStateRepository(db_path=path)
    now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    state = repo.create(project_id=1, operator="@op", ttl_seconds=300, now=now)
    expected_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM calendar_oauth_pending_state WHERE state_hash = ?",
            (expected_hash,),
        ).fetchone()
    assert row is not None
    # Plaintext state is never stored.
    assert state != expected_hash
    assert row["expires_at"] == (now + timedelta(seconds=300)).isoformat()
    assert row["consumed_at"] is None


def test_consume_succeeds_once_then_replay_raises(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    state = repo.create(project_id=7, operator="@op", ttl_seconds=300, now=now)
    pending = repo.consume(state, now=now + timedelta(seconds=10))
    assert isinstance(pending, PendingState)
    assert pending.project_id == 7
    assert pending.operator == "@op"
    assert pending.created_at == now.isoformat()
    assert pending.expires_at == (now + timedelta(seconds=300)).isoformat()
    with pytest.raises(InvalidOAuthState):
        repo.consume(state, now=now + timedelta(seconds=20))


def test_consume_expired_raises(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    state = repo.create(project_id=1, operator="@op", ttl_seconds=300, now=now)
    with pytest.raises(InvalidOAuthState):
        repo.consume(state, now=now + timedelta(seconds=301))


def test_consume_unknown_raises(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    with pytest.raises(InvalidOAuthState):
        repo.consume("never-issued", now=now)


def test_init_schema_idempotent_preserves_rows(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    state = repo.create(project_id=2, operator="@op", ttl_seconds=300, now=now)
    repo.init_schema()
    repo.init_schema()
    pending = repo.consume(state, now=now + timedelta(seconds=5))
    assert pending.project_id == 2
