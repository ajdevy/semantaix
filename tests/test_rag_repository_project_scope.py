"""RagRepository project_id scoping for Epic 10 story 10.06."""

import sqlite3

from services.api.app.rag import RagRepository


def test_ingest_writes_project_id(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="hello world", project_id=7)
    with sqlite3.connect(repo.db_path) as connection:
        rows = connection.execute(
            "SELECT project_id FROM rag_chunks"
        ).fetchall()
    assert rows == [(7,)]


def test_ingest_without_project_id_stores_null(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="hello world")
    with sqlite3.connect(repo.db_path) as connection:
        rows = connection.execute(
            "SELECT project_id FROM rag_chunks"
        ).fetchall()
    assert rows == [(None,)]


def test_retrieve_filters_to_project_or_null(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="одинаковый текст", project_id=1)
    repo.ingest(source_id="b", text="одинаковый текст другого проекта", project_id=2)
    repo.ingest(source_id="c", text="одинаковый текст без проекта")
    project_1 = repo.retrieve(query="одинаковый текст", project_id=1, limit=10)
    source_ids = {chunk.source_id for chunk in project_1}
    assert "a" in source_ids
    assert "c" in source_ids  # NULL fallback
    assert "b" not in source_ids


def test_retrieve_without_project_id_returns_all(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="одинаковый текст", project_id=1)
    repo.ingest(source_id="b", text="одинаковый текст b", project_id=2)
    all_chunks = repo.retrieve(query="одинаковый текст", limit=10)
    assert {c.source_id for c in all_chunks} == {"a", "b"}


def test_rag_chunk_dataclass_exposes_project_id(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="hello", project_id=7)
    chunks = repo.retrieve(query="hello", project_id=7)
    assert chunks and chunks[0].project_id == 7


def test_update_project_id_for_source_updates_existing_rows(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.sqlite3"))
    repo.ingest(source_id="a", text="text a", project_id=1)
    repo.ingest(source_id="a", text="text more", project_id=1)
    affected = repo.update_project_id_for_source(source_id="a", project_id=9)
    assert affected == 2
    with sqlite3.connect(repo.db_path) as connection:
        rows = connection.execute(
            "SELECT project_id FROM rag_chunks WHERE source_id = ?", ("a",)
        ).fetchall()
    assert all(row[0] == 9 for row in rows)
