from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.bot_gateway.app import kb_session
from services.bot_gateway.app.kb_session import (
    OperatorKbSession,
    OperatorKbSessionRepository,
)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "kb_session_test.db")


@pytest.fixture
def repo(db_path: str) -> OperatorKbSessionRepository:
    return OperatorKbSessionRepository(db_path)


def test_upsert_creates_session_with_expected_ttl(repo: OperatorKbSessionRepository):
    session = repo.upsert(
        chat_id=42,
        username="@op",
        is_confidential=False,
        ttl_seconds=600,
    )
    assert isinstance(session, OperatorKbSession)
    assert session.chat_id == 42
    assert session.username == "@op"
    assert session.is_confidential is False

    created = datetime.fromisoformat(session.created_at)
    expires = datetime.fromisoformat(session.expires_at)
    delta = expires - created
    # Allow a small wall-clock window.
    assert timedelta(seconds=599) <= delta <= timedelta(seconds=601)


def test_upsert_refreshes_existing_session(repo: OperatorKbSessionRepository, monkeypatch):
    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(kb_session, "_now", lambda: base)
    first = repo.upsert(chat_id=1, username="@op", is_confidential=False, ttl_seconds=600)

    monkeypatch.setattr(kb_session, "_now", lambda: base + timedelta(seconds=120))
    second = repo.upsert(chat_id=1, username="@op", is_confidential=True, ttl_seconds=600)

    assert second.is_confidential is True
    # Refreshed: new expires_at sits 600s after the new clock, not after the original clock.
    assert datetime.fromisoformat(second.expires_at) == base + timedelta(seconds=720)
    assert second.expires_at != first.expires_at


def test_get_active_returns_session_while_unexpired(
    repo: OperatorKbSessionRepository, monkeypatch
):
    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(kb_session, "_now", lambda: base)
    repo.upsert(chat_id=1, username="@op", is_confidential=True, ttl_seconds=600)

    monkeypatch.setattr(kb_session, "_now", lambda: base + timedelta(seconds=300))
    fetched = repo.get_active(chat_id=1, username="@op")
    assert fetched is not None
    assert fetched.is_confidential is True


def test_get_active_returns_none_after_ttl(
    repo: OperatorKbSessionRepository, monkeypatch
):
    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(kb_session, "_now", lambda: base)
    repo.upsert(chat_id=1, username="@op", is_confidential=False, ttl_seconds=600)

    monkeypatch.setattr(kb_session, "_now", lambda: base + timedelta(seconds=601))
    assert repo.get_active(chat_id=1, username="@op") is None


def test_get_active_returns_none_for_missing_row(repo: OperatorKbSessionRepository):
    assert repo.get_active(chat_id=999, username="@nobody") is None


def test_clear_removes_session(repo: OperatorKbSessionRepository):
    repo.upsert(chat_id=1, username="@op", is_confidential=False, ttl_seconds=600)
    repo.clear(chat_id=1, username="@op")
    assert repo.get_active(chat_id=1, username="@op") is None


def test_clear_is_noop_when_missing(repo: OperatorKbSessionRepository):
    # Must not raise even if there's no row.
    repo.clear(chat_id=1, username="@op")
    assert repo.get_active(chat_id=1, username="@op") is None


def test_sessions_are_isolated_per_chat_and_user(repo: OperatorKbSessionRepository):
    repo.upsert(chat_id=1, username="@op", is_confidential=False, ttl_seconds=600)
    repo.upsert(chat_id=2, username="@op", is_confidential=True, ttl_seconds=600)

    chat1 = repo.get_active(chat_id=1, username="@op")
    chat2 = repo.get_active(chat_id=2, username="@op")
    assert chat1 is not None and chat1.is_confidential is False
    assert chat2 is not None and chat2.is_confidential is True

    other_user = repo.get_active(chat_id=1, username="@other")
    assert other_user is None


def test_init_schema_is_idempotent(db_path: str):
    OperatorKbSessionRepository(db_path)
    # Re-init should not raise.
    OperatorKbSessionRepository(db_path)
    kb_session.init_schema(db_path)
    kb_session.init_schema(db_path)


def test_now_returns_utc_datetime():
    now = kb_session._now()
    assert now.tzinfo is UTC
