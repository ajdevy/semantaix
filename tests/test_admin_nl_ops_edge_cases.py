"""Cover the rare `_get` LookupError + the api 'unconfirmable_op_type' branch."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.admin_nl_ops import (
    OP_CLARIFY,
    STATUS_PENDING,
    AdminNlOpSession,
    AdminNlOpsRepository,
)


def test_internal_get_raises_when_row_disappears(tmp_path, monkeypatch):
    """If a row gets deleted under us between get() and _get(), raise."""
    repo = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    session = repo.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    # Delete the row out from under the repo, then call `_get` directly.
    with sqlite3.connect(repo.db_path) as connection:
        connection.execute(
            "DELETE FROM admin_nl_op_sessions WHERE id = ?", (session.id,)
        )
    with pytest.raises(LookupError):
        repo._get(session.id)


def test_confirm_clarify_op_type_raises_400(tmp_path, monkeypatch):
    """A pending-confirmation session with op_type=clarify should 400."""
    projects = type(api_main.project_repository)(
        str(tmp_path / "projects.sqlite3")
    )
    nl_ops = AdminNlOpsRepository(str(tmp_path / "nl.sqlite3"))
    monkeypatch.setattr(api_main, "project_repository", projects)
    monkeypatch.setattr(api_main, "admin_nl_ops_repository", nl_ops)
    monkeypatch.setattr(api_main.settings, "admin_telegram_username", "@admin")
    monkeypatch.setattr(api_main.settings, "admin_internal_token", "secret")
    projects.ensure_default_project()

    # Force a session with op_type=clarify but status=pending_confirmation.
    propose = nl_ops.propose(
        admin_username="@admin", utterance="создай проект x X"
    )
    with sqlite3.connect(nl_ops.db_path) as connection:
        connection.execute(
            "UPDATE admin_nl_op_sessions SET op_type = ?, status = ? WHERE id = ?",
            (OP_CLARIFY, STATUS_PENDING, propose.id),
        )

    client = TestClient(api_main.app)
    response = client.post(
        f"/admin/nl-ops/{propose.id}/confirm",
        json={"confirm_token": propose.confirm_token or ""},
        headers={"X-Internal-Token": "secret"},
    )
    assert response.status_code == 400
    assert "unconfirmable_op_type" in response.json()["detail"]


def test_session_dataclass_round_trip():
    """Smoke: AdminNlOpSession is a frozen dataclass usable as a tuple proxy."""
    session = AdminNlOpSession(
        id=1,
        admin_username="@admin",
        utterance="x",
        op_type="project_create",
        payload={"slug": "x"},
        status="pending_confirmation",
        confirm_token="tok",
        preview="p",
        created_at="t",
        updated_at="t",
    )
    assert session.id == 1
    assert session.payload == {"slug": "x"}
