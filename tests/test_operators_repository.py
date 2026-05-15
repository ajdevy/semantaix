import sqlite3

import pytest

from services.api.app.operators import (
    Operator,
    OperatorRepository,
    OperatorUsernameConflict,
)


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "operators.sqlite3")
    repository = OperatorRepository(path)
    repository.init_schema()
    repository.init_schema()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(operators)").fetchall()
    names = {row[1] for row in rows}
    assert {
        "id",
        "username",
        "chat_id",
        "project_id",
        "display_name",
        "is_active",
        "created_at",
        "updated_at",
    }.issubset(names)


def test_create_and_find_by_username(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    operator = repository.create(
        username="@op-a",
        project_id=1,
        chat_id=12345,
        display_name="Op A",
    )
    assert isinstance(operator, Operator)
    assert operator.username == "@op-a"
    assert operator.project_id == 1
    assert operator.chat_id == 12345
    assert operator.is_active is True
    fetched = repository.find_by_username("@op-a")
    assert fetched is not None
    assert fetched.id == operator.id


def test_find_by_username_unknown_returns_none(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    assert repository.find_by_username("@ghost") is None


def test_create_duplicate_username_raises(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    with pytest.raises(OperatorUsernameConflict):
        repository.create(username="@op-a", project_id=2)


def test_update_partial_fields(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    initial = repository.create(username="@op-a", project_id=1, chat_id=10)
    updated = repository.update(username="@op-a", project_id=2)
    assert updated.project_id == 2
    assert updated.chat_id == 10
    assert updated.updated_at >= initial.updated_at
    same = repository.update(username="@op-a")
    assert same.project_id == 2


def test_update_unknown_raises_lookup(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    with pytest.raises(LookupError):
        repository.update(username="@nope", project_id=1)


def test_update_toggles_is_active(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    deactivated = repository.update(username="@op-a", is_active=False)
    assert deactivated.is_active is False
    reactivated = repository.update(username="@op-a", is_active=True)
    assert reactivated.is_active is True


def test_list_active_excludes_inactive(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    repository.create(username="@op-b", project_id=1)
    repository.update(username="@op-a", is_active=False)
    active = repository.list_active()
    assert [op.username for op in active] == ["@op-b"]


def test_list_all_includes_inactive(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    repository.create(username="@op-b", project_id=1)
    repository.update(username="@op-a", is_active=False)
    listing = repository.list_all()
    assert {op.username for op in listing} == {"@op-a", "@op-b"}
    statuses = {op.username: op.is_active for op in listing}
    assert statuses["@op-a"] is False
    assert statuses["@op-b"] is True


def test_list_by_project_id(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    repository.create(username="@op-b", project_id=2)
    repository.create(username="@op-c", project_id=1)
    rows = repository.list_by_project_id(1)
    assert {op.username for op in rows} == {"@op-a", "@op-c"}


def test_ensure_default_operator_idempotent_and_creates(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    first = repository.ensure_default_operator(
        username="@primary", project_id=1, chat_id=99
    )
    second = repository.ensure_default_operator(
        username="@primary", project_id=1, chat_id=99
    )
    assert first.id == second.id
    assert first.chat_id == 99


def test_ensure_default_operator_updates_chat_id_when_changed(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.ensure_default_operator(username="@primary", project_id=1, chat_id=99)
    refreshed = repository.ensure_default_operator(
        username="@primary", project_id=1, chat_id=200
    )
    assert refreshed.chat_id == 200


def test_any_referencing_project(tmp_path):
    repository = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    repository.create(username="@op-a", project_id=1)
    assert repository.any_referencing_project(1) is True
    assert repository.any_referencing_project(99) is False
