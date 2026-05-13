from __future__ import annotations

import sqlite3

from services.api.app.knowledge_moderation import (
    KnowledgeModerationRepository,
    init_schema,
)


def test_create_approved_operator_upload_writes_all_columns(tmp_path):
    repo = KnowledgeModerationRepository(str(tmp_path / "k.db"))
    row = repo.create_approved_operator_upload(
        candidate_text="Расписание офиса полное.",
        published_text="Расписание офиса полное.",
        operator_username="@ajdevy",
        is_confidential=True,
        source_file_name="schedule.pdf",
        source_file_type="pdf",
        stored_binary_path="/tmp/x.pdf",
        binary_sha256="abc123",
    )
    assert row.status == "approved"
    assert row.published_text == "Расписание офиса полное."
    assert row.uploaded_by_operator_username == "@ajdevy"
    assert row.is_confidential is True
    assert row.source_file_name == "schedule.pdf"
    assert row.source_file_type == "pdf"
    assert row.stored_binary_path == "/tmp/x.pdf"
    assert row.binary_sha256 == "abc123"


def test_find_by_binary_sha256_hits_and_misses(tmp_path):
    repo = KnowledgeModerationRepository(str(tmp_path / "k.db"))
    repo.create_approved_operator_upload(
        candidate_text="t1",
        published_text="t1",
        operator_username="@op",
        is_confidential=False,
        source_file_name=None,
        source_file_type="txt",
        stored_binary_path=None,
        binary_sha256="hash-1",
    )
    found = repo.find_by_binary_sha256("hash-1")
    assert found is not None
    assert found.binary_sha256 == "hash-1"
    assert repo.find_by_binary_sha256("hash-missing") is None


def test_migration_is_idempotent_on_preexisting_db(tmp_path):
    db_path = str(tmp_path / "k.db")
    KnowledgeModerationRepository(db_path)
    # Running init_schema a second time on an already-migrated DB should
    # be a no-op (no IntegrityError or ALTER duplication).
    init_schema(db_path)
    init_schema(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            r["name"]
            for r in connection.execute(
                "PRAGMA table_info(knowledge_moderation_candidates)"
            ).fetchall()
        }
        for expected in (
            "uploaded_by_operator_username",
            "is_confidential",
            "source_file_name",
            "source_file_type",
            "stored_binary_path",
            "binary_sha256",
        ):
            assert expected in columns


def test_create_pending_still_works_with_new_columns(tmp_path):
    repo = KnowledgeModerationRepository(str(tmp_path / "k.db"))
    row = repo.create_pending(text="простой текст")
    assert row.status == "pending"
    assert row.uploaded_by_operator_username is None
    assert row.is_confidential is False
    assert row.source_file_name is None
    assert row.source_file_type is None
    assert row.stored_binary_path is None
    assert row.binary_sha256 is None


def test_list_by_status_returns_operator_rows(tmp_path):
    repo = KnowledgeModerationRepository(str(tmp_path / "k.db"))
    repo.create_approved_operator_upload(
        candidate_text="x",
        published_text="x",
        operator_username="@op",
        is_confidential=False,
        source_file_name="x.txt",
        source_file_type="txt",
        stored_binary_path="/tmp/x.txt",
        binary_sha256="h",
    )
    rows = repo.list_by_status("approved")
    assert len(rows) == 1
    assert rows[0].source_file_type == "txt"


def test_list_by_status_all_includes_operator_rows(tmp_path):
    repo = KnowledgeModerationRepository(str(tmp_path / "k.db"))
    repo.create_pending(text="pending text body")
    repo.create_approved_operator_upload(
        candidate_text="approved body",
        published_text="approved body",
        operator_username="@op",
        is_confidential=False,
        source_file_name=None,
        source_file_type="inline_text",
        stored_binary_path=None,
        binary_sha256=None,
    )
    rows = repo.list_by_status(None)
    assert len(rows) == 2
