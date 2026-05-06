import sqlite3

import pytest

from services.api.app.knowledge_moderation import KnowledgeModerationRepository


def test_migration_adds_source_extraction_column_legacy_db(tmp_path):
    path = str(tmp_path / "premigrate.sqlite")
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
        pragma = connection.execute(
            "PRAGMA table_info(knowledge_moderation_candidates)"
        ).fetchall()
        names = {row[1] for row in pragma}
    assert "source_extraction_candidate_id" in names


def test_create_pending_links_source_extraction_id(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(
        text="Linked candidate text long enough for moderation flow.",
        source_extraction_candidate_id=42,
    )
    assert created.source_extraction_candidate_id == 42
    assert repository.get(created.id).source_extraction_candidate_id == 42


def test_create_list_and_get(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="  Long enough candidate text for moderation.  ")
    assert created.status == "pending"
    assert created.source_extraction_candidate_id is None
    listed = repository.list_by_status("pending")
    assert len(listed) == 1
    fetched = repository.get(created.id)
    assert fetched.id == created.id


def test_list_by_status_none_returns_all(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    first = repository.create_pending(text="First long candidate text for moderation listing.")
    second = repository.create_pending(text="Second long candidate text for moderation listing.")
    repository.reject(candidate_id=first.id)
    all_rows = repository.list_by_status(None)
    assert len(all_rows) == 2
    statuses = {row.id: row.status for row in all_rows}
    assert statuses[first.id] == "rejected"
    assert statuses[second.id] == "pending"


def test_get_missing_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    with pytest.raises(LookupError):
        repository.get(404)


def test_mark_approved_missing_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    with pytest.raises(LookupError):
        repository.mark_approved(candidate_id=999, published_text="any text here")


def test_mark_approved_not_pending_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    repository.reject(candidate_id=created.id)
    with pytest.raises(ValueError, match="invalid_status"):
        repository.mark_approved(candidate_id=created.id, published_text="x")


def test_reject_missing_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    with pytest.raises(LookupError):
        repository.reject(candidate_id=999)


def test_prepare_and_mark_approve(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    final = repository.prepare_publish_text(candidate_id=created.id, edited_text=None)
    assert final == created.candidate_text
    repository.mark_approved(candidate_id=created.id, published_text=final)
    row = repository.get(created.id)
    assert row.status == "approved"
    assert row.published_text == final


def test_prepare_uses_edited_text(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    final = repository.prepare_publish_text(
        candidate_id=created.id,
        edited_text="Edited text that is definitely long enough.",
    )
    assert "Edited" in final
    repository.mark_approved(candidate_id=created.id, published_text=final)


def test_reject_transition(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    repository.reject(candidate_id=created.id)
    assert repository.get(created.id).status == "rejected"


def test_reject_duplicate_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    repository.reject(candidate_id=created.id)
    with pytest.raises(ValueError, match="invalid_status"):
        repository.reject(candidate_id=created.id)


def test_prepare_not_pending_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    repository.reject(candidate_id=created.id)
    with pytest.raises(ValueError, match="invalid_status"):
        repository.prepare_publish_text(candidate_id=created.id, edited_text=None)


def test_prepare_missing_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    with pytest.raises(LookupError):
        repository.prepare_publish_text(candidate_id=999, edited_text=None)


def test_prepare_empty_publish_text_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="   ")
    assert created.candidate_text == ""
    with pytest.raises(ValueError, match="empty_publish_text"):
        repository.prepare_publish_text(candidate_id=created.id, edited_text=None)


def test_prepare_blank_edited_falls_back_to_original(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "know.sqlite3"))
    created = repository.create_pending(text="Original text for indexing in knowledge base.")
    final = repository.prepare_publish_text(candidate_id=created.id, edited_text="   ")
    assert final == created.candidate_text
