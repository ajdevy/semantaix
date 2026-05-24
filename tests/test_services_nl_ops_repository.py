"""Repository tests for ``ServicesNlOpsRepository`` (Epic 12, story 12.04).

Covers schema bootstrap idempotency, propose (pending vs clarify), the
single-pending-per-(project, operator) invariant, consume happy path +
ownership/token/TTL failures, cancel happy + ownership, latest_pending with
lazy TTL expiry, and the soft-delete state columns (cancellation_reason +
consumed_at).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app.services_nl_ops import (
    CANCEL_REASON_OPERATOR,
    CANCEL_REASON_REPLACED,
    OP_SERVICE_ADD,
    OP_UNKNOWN,
    STATUS_CANCELLED,
    STATUS_CLARIFY,
    STATUS_CONFIRMED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    InvalidConfirmToken,
    NlOpSessionExpired,
    NlOpSessionNotFound,
    NlOpSessionNotOwner,
    NlOpSessionNotPending,
    ServicesNlOpsRepository,
)


def _make_repo(tmp_path, *, ttl: int = 600) -> ServicesNlOpsRepository:
    db_path = str(tmp_path / "nlops.sqlite3")
    return ServicesNlOpsRepository(db_path=db_path, pending_ttl_seconds=ttl)


_NOW = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


# --- schema bootstrap -------------------------------------------------------


def test_init_schema_is_idempotent(tmp_path):
    db_path = str(tmp_path / "nlops.sqlite3")
    ServicesNlOpsRepository(db_path=db_path)
    ServicesNlOpsRepository(db_path=db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert "services_nl_op_sessions" in {row[0] for row in rows}


# --- propose ----------------------------------------------------------------


def test_propose_pending_happy_path_returns_token(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут цена 2000",
        now=_NOW,
    )
    assert session.status == STATUS_PENDING
    assert session.op_type == OP_SERVICE_ADD
    assert session.plaintext_confirm_token  # token returned
    assert session.confirm_token_sha256  # hashed token persisted
    # expires_at = now + ttl
    assert datetime.fromisoformat(session.expires_at) == _NOW + timedelta(seconds=600)


def test_propose_unknown_returns_clarify_without_token(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр и педикюр",
        now=_NOW,
    )
    assert session.status == STATUS_CLARIFY
    assert session.op_type == OP_UNKNOWN
    assert session.plaintext_confirm_token is None
    assert session.confirm_token_sha256 is None
    assert session.payload.get("reason") == "multiple_services_in_one_utterance"


def test_propose_replaces_prior_pending_single_pending_invariant(tmp_path):
    """Two consecutive proposes for same (project, operator) → prior is cancelled."""
    repo = _make_repo(tmp_path)
    first = repo.propose(
        project_id=42,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    second = repo.propose(
        project_id=42,
        originating_operator="@op",
        text="добавь услугу педикюр на 90 минут",
        now=_NOW + timedelta(seconds=5),
    )
    first_after = repo.get(first.id)
    assert first_after.status == STATUS_CANCELLED
    assert first_after.soft_deleted_at == CANCEL_REASON_REPLACED
    assert first_after.confirm_token_sha256 is None
    assert second.status == STATUS_PENDING
    # Latest pending for the pair is the newest row.
    latest = repo.latest_pending(
        project_id=42, operator="@op", now=_NOW + timedelta(seconds=10)
    )
    assert latest is not None
    assert latest.id == second.id


def test_propose_does_not_replace_other_operator(tmp_path):
    repo = _make_repo(tmp_path)
    a = repo.propose(
        project_id=1,
        originating_operator="@op-a",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    b = repo.propose(
        project_id=1,
        originating_operator="@op-b",
        text="добавь услугу педикюр на 90 минут",
        now=_NOW,
    )
    assert repo.get(a.id).status == STATUS_PENDING
    assert repo.get(b.id).status == STATUS_PENDING


def test_propose_clarify_does_not_clear_prior_pending(tmp_path):
    repo = _make_repo(tmp_path)
    pending = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    # OP_UNKNOWN proposal should not touch the prior pending row.
    repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр и педикюр",
        now=_NOW + timedelta(seconds=1),
    )
    assert repo.get(pending.id).status == STATUS_PENDING


# --- consume ----------------------------------------------------------------


def test_consume_happy_path_sets_confirmed_and_consumed_at(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    confirmed = repo.consume(
        session_id=session.id,
        presenter_operator="@op",
        confirm_token=session.plaintext_confirm_token or "",
        now=_NOW + timedelta(seconds=1),
    )
    assert confirmed.status == STATUS_CONFIRMED
    assert confirmed.confirm_token_sha256 is None
    assert confirmed.consumed_at == (_NOW + timedelta(seconds=1)).isoformat()


def test_consume_wrong_token_raises(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    with pytest.raises(InvalidConfirmToken):
        repo.consume(
            session_id=session.id,
            presenter_operator="@op",
            confirm_token="not-the-token",
            now=_NOW + timedelta(seconds=1),
        )


def test_consume_cross_operator_raises_not_owner(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@alice",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    with pytest.raises(NlOpSessionNotOwner):
        repo.consume(
            session_id=session.id,
            presenter_operator="@bob",
            confirm_token=session.plaintext_confirm_token or "",
            now=_NOW + timedelta(seconds=1),
        )


def test_consume_past_ttl_flips_status_and_raises(tmp_path):
    repo = _make_repo(tmp_path, ttl=10)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    with pytest.raises(NlOpSessionExpired):
        repo.consume(
            session_id=session.id,
            presenter_operator="@op",
            confirm_token=session.plaintext_confirm_token or "",
            now=_NOW + timedelta(seconds=60),
        )
    refreshed = repo.get(session.id)
    assert refreshed.status == STATUS_EXPIRED
    assert refreshed.confirm_token_sha256 is None


def test_consume_not_pending_raises(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр и педикюр",
        now=_NOW,
    )
    # clarify status → not pending
    with pytest.raises(NlOpSessionNotPending):
        repo.consume(
            session_id=session.id,
            presenter_operator="@op",
            confirm_token="x",
            now=_NOW,
        )


def test_consume_replay_after_confirm_raises_not_pending(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    repo.consume(
        session_id=session.id,
        presenter_operator="@op",
        confirm_token=session.plaintext_confirm_token or "",
        now=_NOW,
    )
    with pytest.raises(NlOpSessionNotPending):
        repo.consume(
            session_id=session.id,
            presenter_operator="@op",
            confirm_token=session.plaintext_confirm_token or "",
            now=_NOW,
        )


# --- cancel -----------------------------------------------------------------


def test_cancel_pending_marks_cancelled_with_operator_reason(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    cancelled = repo.cancel(session_id=session.id, presenter_operator="@op")
    assert cancelled.status == STATUS_CANCELLED
    assert cancelled.soft_deleted_at == CANCEL_REASON_OPERATOR
    assert cancelled.confirm_token_sha256 is None


def test_cancel_cross_operator_raises_not_owner(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@alice",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    with pytest.raises(NlOpSessionNotOwner):
        repo.cancel(session_id=session.id, presenter_operator="@bob")


def test_cancel_clarify_session_allowed(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр и педикюр",
        now=_NOW,
    )
    cancelled = repo.cancel(session_id=session.id, presenter_operator="@op")
    assert cancelled.status == STATUS_CANCELLED


def test_cancel_already_confirmed_raises_not_pending(tmp_path):
    repo = _make_repo(tmp_path)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    repo.consume(
        session_id=session.id,
        presenter_operator="@op",
        confirm_token=session.plaintext_confirm_token or "",
        now=_NOW,
    )
    with pytest.raises(NlOpSessionNotPending):
        repo.cancel(session_id=session.id, presenter_operator="@op")


# --- latest_pending ---------------------------------------------------------


def test_latest_pending_returns_newest_pending(tmp_path):
    repo = _make_repo(tmp_path)
    a = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    # Replacing keeps only the second pending.
    b = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу педикюр на 90 минут",
        now=_NOW + timedelta(seconds=1),
    )
    latest = repo.latest_pending(
        project_id=1, operator="@op", now=_NOW + timedelta(seconds=2)
    )
    assert latest is not None
    assert latest.id == b.id
    # And the prior was cancelled, not pending.
    assert repo.get(a.id).status == STATUS_CANCELLED


def test_latest_pending_none_when_empty(tmp_path):
    repo = _make_repo(tmp_path)
    assert (
        repo.latest_pending(project_id=99, operator="@op", now=_NOW) is None
    )


def test_latest_pending_lazy_expires_past_ttl(tmp_path):
    repo = _make_repo(tmp_path, ttl=10)
    session = repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    result = repo.latest_pending(
        project_id=1, operator="@op", now=_NOW + timedelta(seconds=60)
    )
    assert result is None
    assert repo.get(session.id).status == STATUS_EXPIRED


# --- get error paths --------------------------------------------------------


def test_get_unknown_session_raises_not_found(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(NlOpSessionNotFound):
        repo.get(999)


def test_propose_rollback_on_insert_error(tmp_path, monkeypatch):
    """If the INSERT inside BEGIN IMMEDIATE raises, the txn is rolled back."""
    repo = _make_repo(tmp_path)
    # Seed one pending row to exercise the prior-row UPDATE before the insert.
    repo.propose(
        project_id=1,
        originating_operator="@op",
        text="добавь услугу маникюр на 60 минут",
        now=_NOW,
    )
    # Monkeypatch sqlite3.Connection.execute to raise specifically on INSERT.
    import services.api.app.services_nl_ops as mod

    real_connect = mod._connect

    class _BadConnection:
        def __init__(self, inner):
            self._inner = inner
            self._calls = 0

        def execute(self, sql, *args, **kwargs):
            self._calls += 1
            if "INSERT INTO services_nl_op_sessions" in sql:
                raise sqlite3.OperationalError("forced-test-failure")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    def fake_connect(db_path):
        return _BadConnection(real_connect(db_path))

    monkeypatch.setattr(mod, "_connect", fake_connect)
    with pytest.raises(sqlite3.OperationalError):
        repo.propose(
            project_id=1,
            originating_operator="@op",
            text="добавь услугу педикюр на 90 минут",
            now=_NOW + timedelta(seconds=2),
        )
