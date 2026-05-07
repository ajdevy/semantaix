from __future__ import annotations

import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import backup_repository, incident_repository


def _wire_repos(tmp_path: Path, sources: list[Path]) -> None:
    backup_repository.db_path = str(tmp_path / "backups.sqlite3")
    backup_repository.archive_dir = str(tmp_path / "archives")
    backup_repository.source_paths = [str(item) for item in sources]
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")


def _seed(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_run_backup_endpoint_creates_archive(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "rag")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)

    response = client.post("/backups/run")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["size_bytes"] > 0
    assert body["included_paths"] == [str(rag)]
    assert Path(body["archive_path"]).exists()


def test_list_backups_returns_recent_first(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "rag")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)

    first = client.post("/backups/run").json()
    second = client.post("/backups/run").json()

    listed = client.get("/backups").json()["items"]
    assert [item["id"] for item in listed] == [second["id"], first["id"]]


def test_get_last_successful_returns_latest_when_none(tmp_path):
    _wire_repos(tmp_path, [])
    client = TestClient(api_app)
    assert client.get("/backups/last-successful").json() == {"backup": None}


def test_get_last_successful_returns_latest(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "rag")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)
    second = client.post("/backups/run").json()
    client.post("/backups/run")

    response = client.get("/backups/last-successful").json()

    assert response["backup"] is not None
    assert response["backup"]["id"] >= second["id"]


def test_get_backup_returns_404_when_missing(tmp_path):
    _wire_repos(tmp_path, [])
    client = TestClient(api_app)
    response = client.get("/backups/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "backup_not_found"


def test_get_backup_returns_record(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "rag")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)
    created = client.post("/backups/run").json()
    fetched = client.get(f"/backups/{created['id']}").json()
    assert fetched["id"] == created["id"]


def test_run_backup_failure_emits_incident(tmp_path, monkeypatch):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "rag")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tarfile, "open", _explode)
    response = client.post("/backups/run")
    assert response.status_code == 500
    assert response.json()["detail"] == "backup_failed"
    incidents = client.get("/incidents/backup_failures").json()["items"]
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "critical"


def test_restore_succeeds_with_valid_token(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "payload")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)
    created = client.post("/backups/run").json()

    target = tmp_path / "restored"
    response = client.post(
        f"/backups/{created['id']}/restore",
        json={"confirm_token": f"restore-{created['id']}", "target_root": str(target)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["backup_id"] == created["id"]
    assert (target / rag.name).read_text(encoding="utf-8") == "payload"


def test_restore_rejects_invalid_token(tmp_path):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "payload")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)
    created = client.post("/backups/run").json()

    response = client.post(
        f"/backups/{created['id']}/restore",
        json={"confirm_token": "nope", "target_root": str(tmp_path / "restored")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_confirm_token"


def test_restore_unknown_backup_returns_404(tmp_path):
    _wire_repos(tmp_path, [])
    client = TestClient(api_app)
    response = client.post(
        "/backups/9999/restore",
        json={"confirm_token": "restore-9999", "target_root": str(tmp_path / "restored")},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "backup_not_found"


def test_restore_non_successful_emits_incident(tmp_path, monkeypatch):
    rag = tmp_path / "src" / "rag.db"
    _seed(rag, "payload")
    _wire_repos(tmp_path, [rag])
    client = TestClient(api_app)

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tarfile, "open", _explode)
    fail_response = client.post("/backups/run")
    assert fail_response.status_code == 500

    monkeypatch.undo()
    failed = client.get("/backups").json()["items"][0]
    target_root = str(tmp_path / "restored")
    response = client.post(
        f"/backups/{failed['id']}/restore",
        json={"confirm_token": f"restore-{failed['id']}", "target_root": target_root},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "backup_restore_failed"
    incidents = client.get("/incidents/backup_restore_failures").json()["items"]
    assert len(incidents) == 1
