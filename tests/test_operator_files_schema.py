import sqlite3

from services.bot_gateway.app.operator_files import OperatorFileRepository
from services.bot_gateway.app.telegram_update import TelegramAttachment


def test_init_schema_adds_project_id_column(tmp_path):
    path = str(tmp_path / "operator_files.sqlite3")
    OperatorFileRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(operator_files)").fetchall()
    names = {row[1] for row in rows}
    assert "project_id" in names


def test_migration_adds_project_id_to_legacy_db(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE operator_files (
                short_id TEXT PRIMARY KEY,
                telegram_file_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                source_message_id INTEGER NOT NULL,
                source_file_name TEXT,
                source_file_type TEXT,
                mime_type TEXT,
                file_size_bytes INTEGER,
                is_confidential INTEGER NOT NULL,
                stored_binary_path TEXT,
                download_status TEXT NOT NULL,
                kb_ingest_status TEXT NOT NULL,
                kb_inserted_chunks INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    OperatorFileRepository(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(operator_files)").fetchall()
    names = {row[1] for row in rows}
    assert "project_id" in names


def test_record_upload_keeps_working_without_project_id(tmp_path):
    repository = OperatorFileRepository(str(tmp_path / "of.sqlite3"))
    attachment = TelegramAttachment(
        file_id="tg-file",
        file_name="hello.txt",
        mime_type="text/plain",
        file_size=10,
        kind="document",
    )
    record = repository.record_upload(
        chat_id=1,
        username="@op-a",
        source_message_id=42,
        attachment=attachment,
        is_confidential=False,
        stored_binary_path=None,
        download_status="ok",
        source_file_type="text",
    )
    assert record.short_id
    assert record.username == "@op-a"
