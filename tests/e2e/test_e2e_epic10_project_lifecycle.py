"""Epic 10 story 10.03: project + operator + file reassign lifecycle via api.

Exercises the full admin CRUD path end-to-end against the api service
(web UI forwarding pattern verified separately in
`tests/test_web_ui_admin.py`).
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

pytestmark = [pytest.mark.e2e, pytest.mark.epic("10")]


@pytest.mark.story("10-03")
def test_project_and_operator_and_file_lifecycle(tmp_path, monkeypatch):
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
    monkeypatch.setattr(api_main, "knowledge_moderation_repository", knowledge)
    monkeypatch.setattr(api_main, "rag_repository", rag)
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "internal")

    client = TestClient(api_main.app)
    headers = {"X-Internal-Token": "internal"}

    # Create project.
    create = client.post(
        "/projects",
        json={"slug": "billing", "name": "Биллинг"},
        headers=headers,
    )
    assert create.status_code == 200
    billing_id = create.json()["id"]

    # Add operator to project.
    client.post(
        "/operators",
        json={
            "username": "@op-b",
            "project_id": billing_id,
            "chat_id": 200,
        },
        headers=headers,
    ).raise_for_status()

    # Upload a file via the admin operator into the default project.
    candidate = knowledge.create_approved_operator_upload(
        candidate_text="Договор поставки",
        published_text="Договор поставки",
        operator_username="@admin",
        is_confidential=False,
        source_file_name="contract.pdf",
        source_file_type="pdf",
        stored_binary_path=None,
        binary_sha256=None,
    )
    rag.ingest(
        source_id=f"knowledge_candidate:{candidate.id}",
        text="Договор поставки",
    )

    # Project detail shows the operator counts.
    detail = client.get("/projects/billing", headers=headers).json()
    assert detail["operator_count"] == 1
    assert detail["operators"][0]["username"] == "@op-b"

    # Reassign the file to billing.
    reassign = client.post(
        f"/knowledge/candidates/{candidate.id}/reassign",
        json={"project_id": billing_id},
        headers=headers,
    )
    assert reassign.status_code == 200

    # Verify the candidate row + rag chunk now point to billing.
    import sqlite3

    with sqlite3.connect(knowledge.db_path) as connection:
        row = connection.execute(
            "SELECT project_id FROM knowledge_moderation_candidates WHERE id = ?",
            (candidate.id,),
        ).fetchone()
        assert row[0] == billing_id
    with sqlite3.connect(rag.db_path) as connection:
        rows = connection.execute(
            "SELECT project_id FROM rag_chunks WHERE source_id = ?",
            (f"knowledge_candidate:{candidate.id}",),
        ).fetchall()
        assert rows
        assert all(r[0] == billing_id for r in rows)

    # Operators list contains both admin + the new one.
    listing = client.get("/operators", headers=headers).json()
    assert {o["username"] for o in listing["items"]} == {"@admin", "@op-b"}

    # Delete the billing project — refused because @op-b is still attached.
    refuse = client.delete("/projects/billing", headers=headers)
    assert refuse.status_code == 409

    # Detach @op-b first, then delete succeeds.
    client.patch(
        "/operators/@op-b",
        json={"project_id": default.id},
        headers=headers,
    ).raise_for_status()
    delete = client.delete("/projects/billing", headers=headers)
    assert delete.status_code == 200
    assert projects.get_by_slug("billing") is None
