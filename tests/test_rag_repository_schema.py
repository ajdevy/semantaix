import sqlite3

from services.api.app.rag import RagRepository


def test_init_schema_adds_project_id_column(tmp_path):
    path = str(tmp_path / "rag.sqlite3")
    RagRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(rag_chunks)").fetchall()
    names = {row[1] for row in rows}
    assert "project_id" in names


def test_init_schema_adds_index_on_project_id(tmp_path):
    path = str(tmp_path / "rag.sqlite3")
    RagRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    names = {row[0] for row in rows}
    assert "idx_rag_chunks_project" in names


def test_migration_adds_project_id_to_legacy_db(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                chunk_hash TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                UNIQUE(source_id, chunk_hash)
            )
            """
        )
    RagRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(rag_chunks)").fetchall()
    names = {row[1] for row in rows}
    assert "is_confidential" in names
    assert "project_id" in names


def test_existing_ingest_and_retrieve_keep_working(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    inserted = repository.ingest(
        source_id="src-a", text="Тестовая строка для проверки извлечения."
    )
    assert inserted == 1
    chunks = repository.retrieve(query="тестовая строка")
    assert len(chunks) == 1
