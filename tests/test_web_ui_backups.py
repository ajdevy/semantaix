from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app.backups import BackupRepository
from services.web_ui.app import main as web_ui_main
from services.web_ui.app.main import app as web_ui_app


def _seed(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_backups_shell_reports_no_backup(monkeypatch, tmp_path):
    repo = BackupRepository(
        db_path=str(tmp_path / "backups.sqlite3"),
        archive_dir=str(tmp_path / "archives"),
        source_paths=[],
    )
    monkeypatch.setattr(web_ui_main, "backup_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/backups")

    assert response.status_code == 200
    assert "No successful backup" in response.text


def test_backups_shell_renders_latest_backup(monkeypatch, tmp_path):
    src = tmp_path / "src" / "rag.db"
    _seed(src, "data")
    repo = BackupRepository(
        db_path=str(tmp_path / "backups.sqlite3"),
        archive_dir=str(tmp_path / "archives"),
        source_paths=[str(src)],
    )
    monkeypatch.setattr(web_ui_main, "backup_repository", repo)
    backup = repo.run_backup()
    client = TestClient(web_ui_app)

    response = client.get("/backups")

    assert response.status_code == 200
    assert "Last successful backup" in response.text
    assert str(backup.id) in response.text
    assert backup.archive_path in response.text
