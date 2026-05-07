from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from services.api.app.backups import BackupError, BackupRepository, init_schema


def _seed_source(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _build_repository(tmp_path: Path, sources: list[Path]) -> BackupRepository:
    return BackupRepository(
        db_path=str(tmp_path / "backups.sqlite3"),
        archive_dir=str(tmp_path / "archives"),
        source_paths=[str(item) for item in sources],
    )


def test_init_schema_is_idempotent(tmp_path):
    db_path = str(tmp_path / "backups.sqlite3")
    init_schema(db_path)
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"backups", "backup_events"}.issubset(tables)


def test_run_backup_archives_existing_sources(tmp_path):
    rag = tmp_path / "data" / "rag.db"
    knowledge = tmp_path / "data" / "knowledge.db"
    missing = tmp_path / "data" / "absent.db"
    _seed_source(rag, "rag-bytes")
    _seed_source(knowledge, "knowledge-bytes")
    repository = _build_repository(tmp_path, [rag, knowledge, missing])

    backup = repository.run_backup()

    assert backup.status == "success"
    assert backup.completed_at is not None
    assert backup.archive_path is not None
    assert backup.size_bytes > 0
    assert backup.error_message is None
    assert sorted(backup.included_paths) == sorted([str(rag), str(knowledge)])
    assert Path(backup.archive_path).exists()
    with tarfile.open(backup.archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert sorted(names) == sorted([rag.name, knowledge.name])


def test_run_backup_skips_when_sources_missing(tmp_path):
    repository = _build_repository(tmp_path, [tmp_path / "ghost.db"])

    backup = repository.run_backup()

    assert backup.status == "success"
    assert backup.included_paths == []
    assert backup.archive_path is not None
    assert Path(backup.archive_path).exists()


def test_get_returns_persisted_backup(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    created = repository.run_backup()

    fetched = repository.get(created.id)

    assert fetched.id == created.id
    assert fetched.status == "success"
    assert fetched.source_paths == [str(rag)]


def test_get_raises_for_unknown_backup(tmp_path):
    repository = _build_repository(tmp_path, [])
    with pytest.raises(LookupError):
        repository.get(999)


def test_list_backups_returns_most_recent_first(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    first = repository.run_backup()
    second = repository.run_backup()

    listed = repository.list_backups()

    assert [item.id for item in listed] == [second.id, first.id]


def test_latest_successful_returns_none_when_empty(tmp_path):
    repository = _build_repository(tmp_path, [])
    assert repository.latest_successful() is None


def test_latest_successful_returns_most_recent_success(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    repository.run_backup()
    second = repository.run_backup()
    assert repository.latest_successful().id == second.id


def test_run_backup_failure_records_failed_status_and_event(tmp_path, monkeypatch):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tarfile, "open", _explode)
    with pytest.raises(BackupError) as excinfo:
        repository.run_backup()
    assert "backup_archive_failed" in str(excinfo.value)

    listed = repository.list_backups()
    assert len(listed) == 1
    assert listed[0].status == "failed"
    assert listed[0].error_message is not None
    assert listed[0].error_message.startswith("backup_archive_failed")


def test_restore_extracts_archive_into_target(tmp_path):
    rag = tmp_path / "data" / "rag.db"
    _seed_source(rag, "rag-bytes")
    repository = _build_repository(tmp_path, [rag])
    backup = repository.run_backup()

    target = tmp_path / "restore"
    result = repository.restore(
        backup_id=backup.id,
        confirm_token=f"restore-{backup.id}",
        target_root=str(target),
    )

    assert result.backup_id == backup.id
    restored_file = target / rag.name
    assert restored_file.exists()
    assert restored_file.read_text(encoding="utf-8") == "rag-bytes"
    assert str(restored_file) in result.restored_paths


def test_restore_rejects_invalid_confirm_token(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    backup = repository.run_backup()

    with pytest.raises(BackupError) as excinfo:
        repository.restore(
            backup_id=backup.id,
            confirm_token="wrong",
            target_root=str(tmp_path / "restore"),
        )
    assert str(excinfo.value) == "invalid_confirm_token"


def test_restore_rejects_non_successful_backup(tmp_path, monkeypatch):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])

    def _explode(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(tarfile, "open", _explode)
    with pytest.raises(BackupError):
        repository.run_backup()
    failed = repository.list_backups()[0]

    with pytest.raises(BackupError) as excinfo:
        repository.restore(
            backup_id=failed.id,
            confirm_token=f"restore-{failed.id}",
            target_root=str(tmp_path / "restore"),
        )
    assert "backup_not_restorable" in str(excinfo.value)


def test_restore_fails_when_archive_missing(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    backup = repository.run_backup()
    Path(backup.archive_path).unlink()

    with pytest.raises(BackupError) as excinfo:
        repository.restore(
            backup_id=backup.id,
            confirm_token=f"restore-{backup.id}",
            target_root=str(tmp_path / "restore"),
        )
    assert str(excinfo.value) == "archive_missing_on_disk"


def test_restore_fails_when_archive_path_blank(tmp_path):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    backup = repository.run_backup()

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            "UPDATE backups SET archive_path = NULL WHERE id = ?",
            (backup.id,),
        )

    with pytest.raises(BackupError) as excinfo:
        repository.restore(
            backup_id=backup.id,
            confirm_token=f"restore-{backup.id}",
            target_root=str(tmp_path / "restore"),
        )
    assert str(excinfo.value) == "missing_archive_path"


def test_restore_emits_failure_event_on_extraction_error(tmp_path, monkeypatch):
    rag = tmp_path / "rag.db"
    _seed_source(rag, "rag")
    repository = _build_repository(tmp_path, [rag])
    backup = repository.run_backup()

    real_open = tarfile.open

    def _selective_open(*args, **kwargs):
        mode = kwargs.get("mode") or (str(args[1]) if len(args) > 1 else "")
        if mode.startswith("r"):
            raise tarfile.TarError("bad archive")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(tarfile, "open", _selective_open)
    with pytest.raises(BackupError) as excinfo:
        repository.restore(
            backup_id=backup.id,
            confirm_token=f"restore-{backup.id}",
            target_root=str(tmp_path / "restore"),
        )
    assert "restore_extract_failed" in str(excinfo.value)
