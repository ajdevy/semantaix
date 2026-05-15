"""Contract tests for admin NL ops api endpoints."""

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.admin_nl_ops import AdminNlOpsRepository
from services.api.app.knowledge_moderation import KnowledgeModerationRepository
from services.api.app.operators import OperatorRepository
from services.api.app.projects import ProjectRepository
from services.api.app.rag import RagRepository


@pytest.fixture
def stack(tmp_path, monkeypatch):
    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    operators = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    nl_ops = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    knowledge = KnowledgeModerationRepository(str(tmp_path / "k.sqlite3"))
    rag = RagRepository(str(tmp_path / "rag.sqlite3"))
    default = projects.ensure_default_project()
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "operator_repository", operators)
    monkeypatch.setattr(api_main, "admin_nl_ops_repository", nl_ops)
    monkeypatch.setattr(
        api_main, "knowledge_moderation_repository", knowledge
    )
    monkeypatch.setattr(api_main, "rag_repository", rag)
    monkeypatch.setattr(api_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "secret")
    client = TestClient(api_main.app)
    headers = {"X-Internal-Token": "secret"}
    return client, headers, projects, operators, knowledge, rag, default


def test_propose_project_create_returns_pending(stack):
    client, headers, *_ = stack
    response = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_confirmation"
    assert body["confirm_token"]


def test_propose_clarify_returns_session(stack):
    client, headers, *_ = stack
    response = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "что-то непонятное"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "clarify"


def test_propose_rejects_non_admin(stack):
    client, headers, *_ = stack
    response = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@other", "utterance": "x"},
        headers=headers,
    )
    assert response.status_code == 403


def test_confirm_dispatches_project_create(stack):
    client, headers, projects, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "создай проект billing Биллинг",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "confirmed"
    assert projects.get_by_slug("billing") is not None


def test_confirm_project_create_conflict_returns_409(stack):
    client, headers, projects, *_ = stack
    projects.create(slug="dup", name="Dup")
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект dup D"},
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 409


def test_confirm_project_rename_404_when_unknown(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "переименуй проект ghost в New",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 404


def test_confirm_project_rename_happy(stack):
    client, headers, projects, *_ = stack
    projects.create(slug="billing", name="Old")
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "переименуй проект billing в Биллинг",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 200
    assert projects.get_by_slug("billing").name == "Биллинг"


def test_confirm_operator_attach(stack):
    client, headers, projects, operators, *_ = stack
    projects.create(slug="billing", name="B")
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "добавь оператора @new в billing 7777",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 200
    op = operators.find_by_username("@new")
    assert op is not None
    assert op.chat_id == 7777


def test_confirm_operator_attach_missing_project(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "добавь оператора @new в ghost",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 400


def test_confirm_operator_attach_duplicate_username(stack):
    client, headers, projects, operators, *_ = stack
    projects.create(slug="billing", name="B")
    operators.create(username="@dup", project_id=projects.get_by_slug("billing").id)
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "добавь оператора @dup в billing",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 409


def test_confirm_operator_detach(stack):
    client, headers, projects, operators, *_ = stack
    projects.create(slug="x", name="X")
    operators.create(username="@op", project_id=projects.get_by_slug("x").id)
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "удали оператора @op"},
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 200
    assert operators.find_by_username("@op").is_active is False


def test_confirm_operator_detach_unknown_returns_404(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "удали оператора @ghost",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 404


def test_confirm_file_attach(stack):
    client, headers, projects, _, knowledge, rag, _ = stack
    projects.create(slug="billing", name="B")
    candidate = knowledge.create_approved_operator_upload(
        candidate_text="text",
        published_text="text",
        operator_username="@admin",
        is_confidential=False,
        source_file_name="x",
        source_file_type="text",
        stored_binary_path=None,
        binary_sha256=None,
        operator_short_id="ABC",
    )
    rag.ingest(source_id=f"knowledge_candidate:{candidate.id}", text="text")
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "привяжи файл #ABC к billing",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 200
    refreshed = knowledge.get(candidate.id)
    assert refreshed.project_id == projects.get_by_slug("billing").id


def test_confirm_file_attach_missing_project(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "привяжи файл #X к ghost",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 400


def test_confirm_file_attach_missing_candidate(stack):
    client, headers, projects, *_ = stack
    projects.create(slug="billing", name="B")
    propose = client.post(
        "/admin/nl-ops",
        json={
            "admin_username": "@admin",
            "utterance": "привяжи файл #ZZZ к billing",
        },
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert confirm.status_code == 404


def test_confirm_wrong_token_returns_401(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    ).json()
    confirm = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": "wrong"},
        headers=headers,
    )
    assert confirm.status_code == 401


def test_confirm_unknown_session_returns_404(stack):
    client, headers, *_ = stack
    response = client.post(
        "/admin/nl-ops/9999/confirm",
        json={"confirm_token": "x"},
        headers=headers,
    )
    assert response.status_code == 404


def test_confirm_already_confirmed_returns_409(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    ).json()
    client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    again = client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    assert again.status_code == 409


def test_cancel_pending_session(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    ).json()
    cancel = client.post(
        f"/admin/nl-ops/{propose['id']}/cancel", headers=headers
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


def test_cancel_unknown_session_returns_404(stack):
    client, headers, *_ = stack
    response = client.post("/admin/nl-ops/999/cancel", headers=headers)
    assert response.status_code == 404


def test_cancel_already_confirmed_returns_409(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    ).json()
    client.post(
        f"/admin/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
        headers=headers,
    )
    response = client.post(
        f"/admin/nl-ops/{propose['id']}/cancel", headers=headers
    )
    assert response.status_code == 409


def test_latest_pending_endpoint(stack):
    client, headers, *_ = stack
    propose = client.post(
        "/admin/nl-ops",
        json={"admin_username": "@admin", "utterance": "создай проект x X"},
        headers=headers,
    ).json()
    response = client.get(
        "/admin/nl-ops/latest-pending?admin_username=@admin", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["id"] == propose["id"]


def test_latest_pending_none_when_empty(stack):
    client, headers, *_ = stack
    response = client.get(
        "/admin/nl-ops/latest-pending?admin_username=@admin", headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {"found": False}
