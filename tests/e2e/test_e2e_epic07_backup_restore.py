"""Epic 07: backup run → list → restore round-trip."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import backup_repository, incident_repository

pytestmark = [pytest.mark.e2e, pytest.mark.epic("07"), pytest.mark.story("07-01")]


def test_epic07_backup_run_then_restore(tmp_path):
    rag_db = tmp_path / "src" / "rag.sqlite3"
    knowledge_db = tmp_path / "src" / "knowledge.sqlite3"
    rag_db.parent.mkdir(parents=True, exist_ok=True)
    rag_db.write_text("rag-data", encoding="utf-8")
    knowledge_db.write_text("knowledge-data", encoding="utf-8")

    backup_repository.db_path = str(tmp_path / "backups.sqlite3")
    backup_repository.archive_dir = str(tmp_path / "archives")
    backup_repository.source_paths = [str(rag_db), str(knowledge_db)]
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    client = TestClient(api_app)

    run = client.post("/backups/run")
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "success"
    backup_id = body["id"]
    assert Path(body["archive_path"]).exists()

    last = client.get("/backups/last-successful").json()["backup"]
    assert last is not None and last["id"] == backup_id

    target = tmp_path / "restored"
    restore = client.post(
        f"/backups/{backup_id}/restore",
        json={
            "confirm_token": f"restore-{backup_id}",
            "target_root": str(target),
        },
    )
    assert restore.status_code == 200
    assert (target / rag_db.name).read_text(encoding="utf-8") == "rag-data"
    assert (target / knowledge_db.name).read_text(encoding="utf-8") == "knowledge-data"
