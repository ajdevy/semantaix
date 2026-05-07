from __future__ import annotations

from fastapi.testclient import TestClient

from platform_common.settings import get_settings
from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    nl_knowledge_ops_repository,
    rag_repository,
)


def _wire(tmp_path) -> None:
    nl_knowledge_ops_repository.db_path = str(tmp_path / "nl_ops.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")


def _settings_override(monkeypatch, **overrides) -> None:
    settings = get_settings()
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


def test_propose_returns_pending_session(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="u1")
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add reset password policy"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "create"
    assert body["status"] == "pending_confirmation"
    assert body["confirm_token"] is not None


def test_propose_uses_default_tenant_when_omitted(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(
        monkeypatch,
        nl_ops_enabled=True,
        nl_ops_admin_user_ids="",
        nl_ops_default_tenant_id="default-tenant",
    )
    client = TestClient(api_app)
    body = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "anyone", "utterance": "add x"},
    ).json()
    assert body["tenant_id"] == "default-tenant"


def test_propose_returns_503_when_disabled(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=False)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add x"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "nl_ops_disabled"


def test_propose_returns_403_for_unauthorized_user(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="u-allowed")
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "stranger", "utterance": "add x"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "nl_ops_user_not_authorized"


def test_propose_returns_400_for_blank_utterance(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "utterance_required"


def test_confirm_publishes_version_and_reindexes(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add reset password help"},
    ).json()
    confirm = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["session"]["status"] == "confirmed"
    assert body["version"]["status"] == "published"
    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset password help", "limit": 5},
    ).json()
    assert any(item["source_id"] == body["version"]["source_id"] for item in retrieve["items"])


def test_confirm_invalid_token_returns_400(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add some text"},
    ).json()
    response = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": "wrong"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_confirm_token"


def test_confirm_already_confirmed_returns_409(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add some text"},
    ).json()
    client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    response = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "already_confirmed"


def test_confirm_clarify_session_returns_409(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "hello world"},
    ).json()
    response = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": "anything"},
    )
    assert response.status_code == 409
    assert "invalid_status" in response.json()["detail"]


def test_confirm_unknown_session_returns_404(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/nl-ops/999/confirm",
        json={"confirm_token": "x"},
    )
    assert response.status_code == 404


def test_confirm_reindex_failure_emits_incident(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add reset link policy"},
    ).json()

    def _explode(*_args, **_kwargs):
        raise RuntimeError("rag offline")

    monkeypatch.setattr(rag_repository, "ingest", _explode)
    response = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "nl_knowledge_reindex_failed"
    incidents = client.get("/incidents/nl_knowledge_reindex_failures").json()["items"]
    assert len(incidents) == 1


def test_confirm_deprecate_does_not_reindex(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "delete old rule"},
    ).json()

    def _fail(*_args, **_kwargs):
        raise AssertionError("ingest should not be called for deprecate path")

    monkeypatch.setattr(rag_repository, "ingest", _fail)
    response = client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    assert response.status_code == 200
    assert response.json()["version"]["status"] == "deprecated"


def test_cancel_transitions_pending_to_cancelled(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add some text"},
    ).json()
    response = client.post(f"/knowledge/nl-ops/{propose['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_invalid_status_returns_409(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add some text"},
    ).json()
    client.post(
        f"/knowledge/nl-ops/{propose['id']}/confirm",
        json={"confirm_token": propose["confirm_token"]},
    )
    response = client.post(f"/knowledge/nl-ops/{propose['id']}/cancel")
    assert response.status_code == 409
    assert "invalid_status" in response.json()["detail"]


def test_cancel_unknown_session_returns_404(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    response = client.post("/knowledge/nl-ops/999/cancel")
    assert response.status_code == 404


def test_get_unknown_session_returns_404(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    response = client.get("/knowledge/nl-ops/9999")
    assert response.status_code == 404


def test_get_known_session_returns_serialized_record(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add policy"},
    ).json()
    fetched = client.get(f"/knowledge/nl-ops/{propose['id']}").json()
    assert fetched["id"] == propose["id"]
    assert fetched["intent"] == "create"


def test_list_endpoints_return_recent_first(tmp_path, monkeypatch):
    _wire(tmp_path)
    _settings_override(monkeypatch, nl_ops_enabled=True, nl_ops_admin_user_ids="")
    client = TestClient(api_app)
    a = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add a"},
    ).json()
    b = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add b"},
    ).json()
    sessions = client.get("/knowledge/nl-ops").json()["items"]
    assert [s["id"] for s in sessions[:2]] == [b["id"], a["id"]]
    client.post(
        f"/knowledge/nl-ops/{a['id']}/confirm",
        json={"confirm_token": a["confirm_token"]},
    )
    versions = client.get("/knowledge/versions").json()["items"]
    assert versions[0]["nl_session_id"] == a["id"]
    audit = client.get("/knowledge/nl-ops-audit").json()["items"]
    assert any(item["op_type"] == "confirmed" for item in audit)
    by_tenant = client.get(
        "/knowledge/nl-ops-audit",
        params={"tenant_id": sessions[0]["tenant_id"]},
    ).json()["items"]
    assert all(item["tenant_id"] == sessions[0]["tenant_id"] for item in by_tenant)
    by_tenant_versions = client.get(
        "/knowledge/versions",
        params={"tenant_id": sessions[0]["tenant_id"]},
    ).json()["items"]
    assert all(item["tenant_id"] == sessions[0]["tenant_id"] for item in by_tenant_versions)
    by_tenant_sessions = client.get(
        "/knowledge/nl-ops",
        params={"tenant_id": sessions[0]["tenant_id"]},
    ).json()["items"]
    assert all(item["tenant_id"] == sessions[0]["tenant_id"] for item in by_tenant_sessions)
