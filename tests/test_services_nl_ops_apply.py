"""Tests for ``apply_confirmed`` side-effect dispatcher (Epic 13, story 13.04).

Exercises the OP_SERVICE_ADD / OP_SERVICE_EDIT / OP_SERVICE_REMOVE dispatch
against a real ``ProjectServiceRepository`` plus the per-(project, lower(name))
``asyncio.Lock`` contract via a counting fake lock factory.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from services.api.app.calendar.project_services_repository import (
    ProjectServiceNotFound,
    ProjectServiceRepository,
)
from services.api.app.services_nl_ops import (
    OP_SERVICE_ADD,
    OP_SERVICE_EDIT,
    OP_SERVICE_REMOVE,
    STATUS_CONFIRMED,
    ServicesNlSession,
    apply_confirmed,
)


def _session(
    *,
    op_type: str,
    project_id: int,
    name: str,
    extra_payload: dict | None = None,
) -> ServicesNlSession:
    payload: dict = {"name": name}
    if extra_payload:
        payload.update(extra_payload)
    return ServicesNlSession(
        id=1,
        project_id=project_id,
        originating_operator="@op",
        op_type=op_type,
        payload=payload,
        preview="preview",
        status=STATUS_CONFIRMED,
        confirm_token_sha256=None,
        created_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:10:00+00:00",
        consumed_at="2026-01-01T00:01:00+00:00",
        soft_deleted_at=None,
    )


class CountingLockFactory:
    """A test double for ``acquire_service_upsert_lock``.

    Mirrors the real factory's per-(project_id, lower(name)) keying.
    Each unique key gets exactly one ``asyncio.Lock``. ``calls`` records
    each invocation so tests can assert reuse.
    """

    def __init__(self) -> None:
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        self.calls: list[tuple[int, str]] = []
        self.call_counts: dict[tuple[int, str], int] = defaultdict(int)

    async def __call__(self, *, project_id: int, name: str) -> asyncio.Lock:
        key = (project_id, name.casefold())
        self.calls.append(key)
        self.call_counts[key] += 1
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


@pytest.fixture
def repo(tmp_path) -> ProjectServiceRepository:
    return ProjectServiceRepository(db_path=str(tmp_path / "calendar.sqlite3"))


# --- ADD / EDIT / REMOVE dispatch ------------------------------------------


def test_apply_add_inserts_row(repo):
    factory = CountingLockFactory()
    session = _session(
        op_type=OP_SERVICE_ADD,
        project_id=1,
        name="маникюр",
        extra_payload={
            "duration_minutes": 60,
            "price_text": "2000",
            "description": "классический",
            "service_days": ["mon", "tue"],
            "working_hours": {"mon": [["10:00", "19:00"]]},
        },
    )
    applied_id = asyncio.run(
        apply_confirmed(session=session, repo=repo, lock_factory=factory)
    )
    row = repo.get_by_name(project_id=1, name="маникюр")
    assert row is not None
    assert row.id == applied_id
    assert row.duration_minutes == 60
    assert row.price_text == "2000"
    assert row.description == "классический"
    assert row.service_days == ["mon", "tue"]


def test_apply_edit_updates_existing_row(repo):
    repo.upsert(
        project_id=1,
        name="маникюр",
        price_text="1500",
        duration_minutes=45,
    )
    factory = CountingLockFactory()
    session = _session(
        op_type=OP_SERVICE_EDIT,
        project_id=1,
        name="маникюр",
        extra_payload={"price_text": "2500"},
    )
    applied_id = asyncio.run(
        apply_confirmed(session=session, repo=repo, lock_factory=factory)
    )
    row = repo.get_by_name(project_id=1, name="маникюр")
    assert row is not None
    assert row.id == applied_id
    assert row.price_text == "2500"


def test_apply_remove_deletes_row(repo):
    repo.upsert(project_id=1, name="маникюр", duration_minutes=60)
    factory = CountingLockFactory()
    session = _session(
        op_type=OP_SERVICE_REMOVE,
        project_id=1,
        name="маникюр",
    )
    applied_id = asyncio.run(
        apply_confirmed(session=session, repo=repo, lock_factory=factory)
    )
    assert applied_id is not None
    assert repo.get_by_name(project_id=1, name="маникюр") is None


def test_apply_remove_missing_row_raises(repo):
    factory = CountingLockFactory()
    session = _session(
        op_type=OP_SERVICE_REMOVE,
        project_id=1,
        name="ghost",
    )
    with pytest.raises(ProjectServiceNotFound):
        asyncio.run(
            apply_confirmed(session=session, repo=repo, lock_factory=factory)
        )


def test_apply_missing_name_raises_value_error(repo):
    factory = CountingLockFactory()
    session = ServicesNlSession(
        id=1,
        project_id=1,
        originating_operator="@op",
        op_type=OP_SERVICE_ADD,
        payload={"name": "   "},
        preview="x",
        status=STATUS_CONFIRMED,
        confirm_token_sha256=None,
        created_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T00:10:00+00:00",
        consumed_at="2026-01-01T00:01:00+00:00",
        soft_deleted_at=None,
    )
    with pytest.raises(ValueError):
        asyncio.run(
            apply_confirmed(session=session, repo=repo, lock_factory=factory)
        )


# --- lock factory contract --------------------------------------------------


def test_lock_factory_returns_same_lock_for_same_project_and_name(repo):
    """Two upserts for the same (project, lower(name)) share one lock."""
    factory = CountingLockFactory()
    s1 = _session(op_type=OP_SERVICE_ADD, project_id=1, name="маникюр")
    s2 = _session(op_type=OP_SERVICE_ADD, project_id=1, name="МАНИКЮР")
    asyncio.run(apply_confirmed(session=s1, repo=repo, lock_factory=factory))
    asyncio.run(apply_confirmed(session=s2, repo=repo, lock_factory=factory))
    # Two calls, one unique key (case-folded).
    assert len(factory.calls) == 2
    assert factory.call_counts[(1, "маникюр")] == 2
    assert len(factory._locks) == 1


def test_lock_factory_returns_different_lock_for_different_keys(repo):
    factory = CountingLockFactory()
    a = _session(op_type=OP_SERVICE_ADD, project_id=1, name="маникюр")
    b = _session(op_type=OP_SERVICE_ADD, project_id=2, name="маникюр")
    c = _session(op_type=OP_SERVICE_ADD, project_id=1, name="педикюр")
    asyncio.run(apply_confirmed(session=a, repo=repo, lock_factory=factory))
    asyncio.run(apply_confirmed(session=b, repo=repo, lock_factory=factory))
    asyncio.run(apply_confirmed(session=c, repo=repo, lock_factory=factory))
    # Three distinct keys → three locks.
    assert len(factory._locks) == 3


def test_apply_emits_confirmed_log_with_full_payload(repo, caplog):
    factory = CountingLockFactory()
    session = _session(
        op_type=OP_SERVICE_ADD,
        project_id=7,
        name="массаж",
        extra_payload={
            "duration_minutes": 90,
            "price_text": "3000",
            "description": "релакс",
            "tags": ["spa"],
            "service_days": ["mon"],
            "working_hours": {"mon": [["10:00", "20:00"]]},
            "date_exceptions": [{"date": "2026-01-01", "closed": True}],
        },
    )
    with caplog.at_level("INFO"):
        asyncio.run(
            apply_confirmed(session=session, repo=repo, lock_factory=factory)
        )
    confirmed_records = [
        r for r in caplog.records if r.message == "services_nl_op_confirmed"
    ]
    assert confirmed_records, "expected services_nl_op_confirmed log"
    record = confirmed_records[-1]
    # The log carries the FULL payload (H5 decision: non-secret).
    # ``service_name`` is used because ``name`` is reserved on LogRecord.
    assert record.service_name == "массаж"
    assert record.description == "релакс"
    assert record.price_text == "3000"
    assert record.tags_json == ["spa"]
    assert record.duration_minutes == 90
    assert record.working_hours_json == {"mon": [["10:00", "20:00"]]}
    assert record.service_days_json == ["mon"]
    assert record.date_exceptions_json == [
        {"date": "2026-01-01", "closed": True}
    ]
    assert record.op_type == OP_SERVICE_ADD
    assert record.applied_service_id is not None
