"""Epic 08 Story 03: NL knowledge ops preview → confirm → reindex round-trip."""

import pytest
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

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-03")]


def test_epic08_nl_op_preview_confirm_reindex(tmp_path, monkeypatch):
    nl_knowledge_ops_repository.db_path = str(tmp_path / "nl_ops.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    settings = get_settings()
    monkeypatch.setattr(settings, "nl_ops_enabled", True)
    monkeypatch.setattr(settings, "nl_ops_admin_user_ids", "u1")

    client = TestClient(api_app)

    propose = client.post(
        "/knowledge/nl-ops",
        json={"user_id": "u1", "utterance": "add reset password requires email link"},
    )
    assert propose.status_code == 200
    proposed = propose.json()
    assert proposed["intent"] == "create"
    assert proposed["status"] == "pending_confirmation"

    confirm = client.post(
        f"/knowledge/nl-ops/{proposed['id']}/confirm",
        json={"confirm_token": proposed["confirm_token"]},
    )
    assert confirm.status_code == 200
    confirmed = confirm.json()
    assert confirmed["session"]["status"] == "confirmed"
    version = confirmed["version"]
    assert version["status"] == "published"

    retrieve = client.post(
        "/rag/retrieve",
        json={"query": "reset password requires email", "limit": 5},
    )
    assert retrieve.status_code == 200
    sources = [item["source_id"] for item in retrieve.json()["items"]]
    assert version["source_id"] in sources

    audit = client.get("/knowledge/nl-ops-audit").json()["items"]
    op_types = {entry["op_type"] for entry in audit}
    assert {"preview_created", "confirmed"}.issubset(op_types)
