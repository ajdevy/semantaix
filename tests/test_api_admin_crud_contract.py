"""Contract tests for admin CRUD endpoints landing in Epic 10 story 10.03.

Covers /projects, /operators, /operators/by-username/{u},
/knowledge/candidates/{id}/reassign, and shared auth modes (admin session
cookie/header + X-Internal-Token).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.admin_auth import AdminAuthRepository
from services.api.app.knowledge_moderation import KnowledgeModerationRepository
from services.api.app.operators import OperatorRepository
from services.api.app.projects import ProjectRepository
from services.api.app.rag import RagRepository


def _seed(tmp_path, monkeypatch):
    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    operators = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    admin_auth = AdminAuthRepository(str(tmp_path / "admin.sqlite3"))
    knowledge = KnowledgeModerationRepository(str(tmp_path / "knowledge.sqlite3"))
    rag = RagRepository(str(tmp_path / "rag.sqlite3"))
    default = projects.ensure_default_project()
    operators.ensure_default_operator(
        username="@admin", project_id=default.id, chat_id=99
    )
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "operator_repository", operators)
    monkeypatch.setattr(api_main, "admin_auth_repository", admin_auth)
    monkeypatch.setattr(
        api_main, "knowledge_moderation_repository", knowledge
    )
    monkeypatch.setattr(api_main, "rag_repository", rag)
    monkeypatch.setattr(api_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(
        api_main.settings, "admin_internal_token", "internal-secret"
    )
    return projects, operators, admin_auth, knowledge, rag, default


def _admin_session_header(admin_auth, operators):
    code = admin_auth.request_code(admin_username="@admin", ttl_seconds=300)
    session = admin_auth.consume_code(
        admin_username="@admin", code=code, ttl_seconds=86400
    )
    return {"X-Admin-Session": session.token}


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    projects, operators, admin_auth, knowledge, rag, default = _seed(
        tmp_path, monkeypatch
    )
    client = TestClient(api_main.app)
    headers = _admin_session_header(admin_auth, operators)
    return client, headers, projects, operators, knowledge, rag, default


@pytest.fixture
def internal_client(tmp_path, monkeypatch):
    projects, operators, admin_auth, knowledge, rag, default = _seed(
        tmp_path, monkeypatch
    )
    client = TestClient(api_main.app)
    headers = {"X-Internal-Token": "internal-secret"}
    return client, headers, projects, operators, knowledge, rag, default


# -- Projects ---------------------------------------------------------------


def test_list_projects_requires_auth(admin_client):
    client, _, _, _, _, _, _ = admin_client
    assert client.get("/projects").status_code == 401


def test_list_projects_admin_session(admin_client):
    client, headers, _, _, _, _, default = admin_client
    response = client.get("/projects", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert any(p["slug"] == "default" for p in body["items"])


def test_list_projects_internal_token(internal_client):
    client, headers, _, _, _, _, _ = internal_client
    response = client.get("/projects", headers=headers)
    assert response.status_code == 200


def test_create_project(admin_client):
    client, headers, projects, _, _, _, _ = admin_client
    response = client.post(
        "/projects",
        json={"slug": "billing", "name": "Биллинг", "description": "ops"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "billing"
    assert projects.get_by_slug("billing") is not None


def test_create_project_duplicate_slug_returns_409(admin_client):
    client, headers, projects, _, _, _, _ = admin_client
    projects.create(slug="billing", name="Биллинг")
    response = client.post(
        "/projects",
        json={"slug": "billing", "name": "Other"},
        headers=headers,
    )
    assert response.status_code == 409


def test_get_project_detail_includes_operator_count(admin_client):
    client, headers, projects, operators, _, _, _ = admin_client
    project = projects.create(slug="billing", name="Биллинг")
    operators.create(username="@op-a", project_id=project.id)
    operators.create(username="@op-b", project_id=project.id)
    response = client.get("/projects/billing", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "billing"
    assert body["operator_count"] == 2


def test_get_project_unknown_returns_404(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.get("/projects/ghost", headers=headers)
    assert response.status_code == 404


def test_patch_project(admin_client):
    client, headers, projects, _, _, _, _ = admin_client
    projects.create(slug="billing", name="Old")
    response = client.patch(
        "/projects/billing",
        json={"name": "New", "description": "renamed"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New"


def test_patch_project_unknown_returns_404(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.patch(
        "/projects/ghost", json={"name": "x"}, headers=headers
    )
    assert response.status_code == 404


def test_delete_project_refuses_when_referenced(admin_client):
    client, headers, projects, operators, _, _, _ = admin_client
    project = projects.create(slug="billing", name="Биллинг")
    operators.create(username="@op-x", project_id=project.id)
    response = client.delete("/projects/billing", headers=headers)
    assert response.status_code == 409
    assert projects.get_by_slug("billing") is not None


def test_delete_project_unknown_returns_404(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.delete("/projects/ghost", headers=headers)
    assert response.status_code == 404


def test_delete_project_happy(admin_client):
    client, headers, projects, _, _, _, _ = admin_client
    projects.create(slug="standalone", name="S")
    response = client.delete("/projects/standalone", headers=headers)
    assert response.status_code == 200
    assert projects.get_by_slug("standalone") is None


# -- Operators --------------------------------------------------------------


def test_list_operators_admin(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.get("/operators", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert any(o["username"] == "@admin" for o in body["items"])


def test_create_operator(admin_client):
    client, headers, _, operators, _, _, default = admin_client
    response = client.post(
        "/operators",
        json={
            "username": "@new_op",
            "project_id": default.id,
            "chat_id": 1234,
            "display_name": "New Op",
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "@new_op"
    assert operators.find_by_username("@new_op") is not None


def test_create_operator_duplicate_returns_409(admin_client):
    client, headers, _, operators, _, _, default = admin_client
    operators.create(username="@dup", project_id=default.id)
    response = client.post(
        "/operators",
        json={"username": "@dup", "project_id": default.id},
        headers=headers,
    )
    assert response.status_code == 409


def test_create_operator_unknown_project_returns_400(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.post(
        "/operators",
        json={"username": "@x", "project_id": 999},
        headers=headers,
    )
    assert response.status_code == 400


def test_get_operator_by_username_is_unauthenticated(admin_client):
    """Used by bot_gateway; intentionally not behind admin auth."""
    client, _, _, _, _, _, _ = admin_client
    response = client.get("/operators/by-username/@admin")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "@admin"


def test_get_operator_by_username_unknown_returns_404(admin_client):
    client, _, _, _, _, _, _ = admin_client
    assert client.get("/operators/by-username/@ghost").status_code == 404


def test_patch_operator(admin_client):
    client, headers, _, operators, _, _, default = admin_client
    operators.create(username="@op-a", project_id=default.id)
    response = client.patch(
        "/operators/@op-a",
        json={"chat_id": 555, "is_active": False},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == 555
    assert body["is_active"] is False


def test_patch_operator_unknown_returns_404(admin_client):
    client, headers, _, _, _, _, _ = admin_client
    response = client.patch(
        "/operators/@ghost", json={"chat_id": 1}, headers=headers
    )
    assert response.status_code == 404


def test_patch_operator_unknown_project_returns_400(admin_client):
    client, headers, _, operators, _, _, default = admin_client
    operators.create(username="@op-a", project_id=default.id)
    response = client.patch(
        "/operators/@op-a", json={"project_id": 999}, headers=headers
    )
    assert response.status_code == 400


# -- Knowledge reassign -----------------------------------------------------


def test_reassign_candidate_moves_chunks(admin_client):
    import sqlite3

    client, headers, projects, _, knowledge, rag, default = admin_client
    candidate = knowledge.create_approved_operator_upload(
        candidate_text="hello",
        published_text="hello",
        operator_username="@admin",
        is_confidential=False,
        source_file_name=None,
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
    )
    rag.ingest(source_id=f"knowledge_candidate:{candidate.id}", text="hello world")
    new_project = projects.create(slug="billing", name="Биллинг")

    response = client.post(
        f"/knowledge/candidates/{candidate.id}/reassign",
        json={"project_id": new_project.id},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == new_project.id

    with sqlite3.connect(knowledge.db_path) as connection:
        row = connection.execute(
            "SELECT project_id FROM knowledge_moderation_candidates WHERE id = ?",
            (candidate.id,),
        ).fetchone()
    assert row[0] == new_project.id

    with sqlite3.connect(rag.db_path) as connection:
        rows = connection.execute(
            "SELECT project_id FROM rag_chunks WHERE source_id = ?",
            (f"knowledge_candidate:{candidate.id}",),
        ).fetchall()
    assert rows
    assert all(r[0] == new_project.id for r in rows)


def test_reassign_unknown_candidate_returns_404(admin_client):
    client, headers, projects, _, _, _, _ = admin_client
    new_project = projects.create(slug="billing", name="x")
    response = client.post(
        "/knowledge/candidates/999/reassign",
        json={"project_id": new_project.id},
        headers=headers,
    )
    assert response.status_code == 404


def test_reassign_unknown_project_returns_400(admin_client):
    client, headers, _, _, knowledge, _, _ = admin_client
    candidate = knowledge.create_approved_operator_upload(
        candidate_text="hi",
        published_text="hi",
        operator_username="@admin",
        is_confidential=False,
        source_file_name=None,
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
    )
    response = client.post(
        f"/knowledge/candidates/{candidate.id}/reassign",
        json={"project_id": 999},
        headers=headers,
    )
    assert response.status_code == 400


def test_internal_token_invalid_value_rejected(admin_client):
    client, _, _, _, _, _, _ = admin_client
    response = client.get(
        "/projects", headers={"X-Internal-Token": "wrong"}
    )
    assert response.status_code == 401


def test_internal_token_disabled_when_empty(tmp_path, monkeypatch):
    """If admin_internal_token is unset, the X-Internal-Token mode is disabled."""
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "")
    client = TestClient(api_main.app)
    response = client.get(
        "/projects", headers={"X-Internal-Token": ""}
    )
    assert response.status_code == 401
