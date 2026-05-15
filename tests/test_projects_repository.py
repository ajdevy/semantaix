import sqlite3

import pytest

from services.api.app.projects import (
    Project,
    ProjectReferenced,
    ProjectRepository,
    ProjectSlugConflict,
)


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "projects.sqlite3")
    repository = ProjectRepository(path)
    repository.init_schema()
    repository.init_schema()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(projects)").fetchall()
    names = {row[1] for row in rows}
    assert {"id", "slug", "name", "description", "created_at", "updated_at"}.issubset(names)


def test_create_and_get_by_slug_round_trip(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    project = repository.create(slug="billing", name="Биллинг", description="команда")
    assert isinstance(project, Project)
    assert project.slug == "billing"
    assert project.name == "Биллинг"
    assert project.description == "команда"
    fetched = repository.get_by_slug("billing")
    assert fetched is not None
    assert fetched.id == project.id


def test_get_returns_project_by_id(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    project = repository.create(slug="alpha", name="Alpha")
    fetched = repository.get(project.id)
    assert fetched is not None
    assert fetched.slug == "alpha"


def test_get_unknown_returns_none(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    assert repository.get_by_slug("nope") is None
    assert repository.get(123) is None


def test_create_duplicate_slug_raises(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    repository.create(slug="billing", name="Биллинг")
    with pytest.raises(ProjectSlugConflict):
        repository.create(slug="billing", name="Другой")


def test_update_changes_name_and_bumps_updated_at(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    initial = repository.create(slug="billing", name="Биллинг")
    updated = repository.update(slug="billing", name="Биллинг 2", description="new desc")
    assert updated.name == "Биллинг 2"
    assert updated.description == "new desc"
    assert updated.updated_at >= initial.updated_at


def test_update_unknown_raises_lookup(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    with pytest.raises(LookupError):
        repository.update(slug="missing", name="x")


def test_update_partial_keeps_other_fields(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    repository.create(slug="b", name="Old", description="old desc")
    updated = repository.update(slug="b", name="New Name")
    assert updated.description == "old desc"
    fetched = repository.get_by_slug("b")
    assert fetched is not None
    assert fetched.description == "old desc"
    # No-op update returns project untouched
    same = repository.update(slug="b")
    assert same.name == "New Name"


def test_list_all_orders_by_id(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    first = repository.create(slug="a", name="A")
    second = repository.create(slug="b", name="B")
    listing = repository.list_all()
    assert [p.id for p in listing] == [first.id, second.id]


def test_delete_removes_project(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    repository.create(slug="x", name="X")
    repository.delete("x")
    assert repository.get_by_slug("x") is None


def test_delete_when_referenced_raises(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    project = repository.create(slug="x", name="X")
    with pytest.raises(ProjectReferenced):
        repository.delete("x", is_referenced=lambda project_id: project_id == project.id)


def test_delete_unknown_raises(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    with pytest.raises(LookupError):
        repository.delete("ghost")


def test_ensure_default_project_idempotent(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    first = repository.ensure_default_project()
    second = repository.ensure_default_project()
    assert first.id == second.id
    assert first.slug == "default"
    assert repository.list_all() == [first]


def test_ensure_default_project_does_not_overwrite_existing(tmp_path):
    repository = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    repository.create(slug="default", name="Custom Default")
    project = repository.ensure_default_project()
    assert project.name == "Custom Default"
