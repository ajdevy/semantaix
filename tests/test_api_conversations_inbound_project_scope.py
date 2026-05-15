"""Verify /conversations/inbound resolves project_id from the latest ticket."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.answerers import AnswerResult
from services.api.app.hitl import HitlTicketRepository
from services.api.app.operators import OperatorRepository
from services.api.app.projects import ProjectRepository


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    projects = ProjectRepository(str(tmp_path / "projects.sqlite3"))
    operators = OperatorRepository(str(tmp_path / "operators.sqlite3"))
    hitl = HitlTicketRepository(str(tmp_path / "hitl.sqlite3"))
    default = projects.ensure_default_project()
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "operator_repository", operators)
    monkeypatch.setattr(api_main, "hitl_ticket_repository", hitl)
    monkeypatch.setattr(
        api_main.answer_pipeline,
        "run",
        AsyncMock(return_value=AnswerResult(handled=False)),
    )
    monkeypatch.setattr(
        api_main, "_safe_send_message", AsyncMock(return_value=True)
    )
    return {"projects": projects, "operators": operators, "hitl": hitl, "default": default}


def test_inbound_resolves_default_project_when_no_ticket(isolated_paths):
    captured: dict = {}

    def capturing_build_ctx(*, chat_id, customer_username, trace_id, now):
        ctx = api_main._build_answer_context(
            chat_id=chat_id,
            customer_username=customer_username,
            trace_id=trace_id,
            now=now,
        )
        captured["project_id"] = ctx.project_id
        return ctx

    api_main._build_answer_context  # ensure exists
    # Resolve via the actual helper.
    project_id = api_main._resolve_inbound_project_id(chat_id=12345)
    assert project_id == isolated_paths["default"].id


def test_inbound_resolves_project_from_operator(isolated_paths):
    billing = isolated_paths["projects"].create(slug="billing", name="B")
    isolated_paths["operators"].create(username="@op-b", project_id=billing.id)
    ticket = isolated_paths["hitl"].create(
        conversation_ref="conv-1",
        reason="needs help",
        target_chat_id=42,
    )
    isolated_paths["hitl"].assign(
        ticket_id=ticket.id, operator_username="@op-b"
    )
    project_id = api_main._resolve_inbound_project_id(chat_id=42)
    assert project_id == billing.id


def test_inbound_resolves_default_when_operator_unknown(isolated_paths):
    ticket = isolated_paths["hitl"].create(
        conversation_ref="conv-2",
        reason="needs help",
        target_chat_id=77,
    )
    isolated_paths["hitl"].assign(
        ticket_id=ticket.id, operator_username="@ghost"
    )
    project_id = api_main._resolve_inbound_project_id(chat_id=77)
    assert project_id == isolated_paths["default"].id


def test_inbound_resolves_default_when_chat_id_is_none(isolated_paths):
    project_id = api_main._resolve_inbound_project_id(chat_id=None)
    assert project_id == isolated_paths["default"].id


def test_default_project_id_returns_none_when_not_seeded(tmp_path, monkeypatch):
    projects = ProjectRepository(str(tmp_path / "p.sqlite3"))
    # NB: do NOT call ensure_default_project — simulates a degraded state.
    monkeypatch.setattr(api_main, "project_repository", projects)
    assert api_main._default_project_id() is None


def test_inbound_endpoint_round_trip_default_project(isolated_paths):
    client = TestClient(api_main.app)
    response = client.post(
        "/conversations/inbound",
        json={"text": "hello", "chat_id": 99},
    )
    assert response.status_code == 200


def test_resolve_inbound_handles_legacy_repo_without_latest_for_chat(
    monkeypatch,
    isolated_paths,
):
    """Older HitlTicketRepository without latest_for_chat falls back gracefully."""

    class LegacyHitl:
        pass

    monkeypatch.setattr(api_main, "hitl_ticket_repository", LegacyHitl())
    project_id = api_main._resolve_inbound_project_id(chat_id=1)
    assert project_id == isolated_paths["default"].id
