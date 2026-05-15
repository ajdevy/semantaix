import sqlite3

from services.api.app.knowledge_moderation import KnowledgeModerationRepository


def test_init_schema_adds_project_id_column(tmp_path):
    path = str(tmp_path / "km.sqlite3")
    KnowledgeModerationRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(knowledge_moderation_candidates)"
        ).fetchall()
    names = {row[1] for row in rows}
    assert "project_id" in names


def test_migration_adds_project_id_to_legacy_db(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE knowledge_moderation_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_text TEXT NOT NULL,
                published_text TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    KnowledgeModerationRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "PRAGMA table_info(knowledge_moderation_candidates)"
        ).fetchall()
    names = {row[1] for row in rows}
    assert "project_id" in names


def test_create_pending_keeps_working(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "km.sqlite3"))
    candidate = repository.create_pending(text="Текст для индексации в базе знаний.")
    assert candidate.status == "pending"
