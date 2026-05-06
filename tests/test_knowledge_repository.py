import sqlite3

from services.api.app.knowledge import (
    KnowledgeCandidateRepository,
    extract_candidate_lines,
    is_noise_text,
)


def _init_transcript_db(path: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO messages (
                conversation_id, source_message_id, role, text, trace_id, created_at
            )
            VALUES
                (1, 10, 'user', 'hi', 't1', '2026-01-01T00:00:00Z'),
                (
                    1, 11, 'user', 'To reset password, use the email link in settings.',
                    't2', '2026-01-01T00:00:01Z'
                ),
                (1, 12, 'user', 'Thanks', 't3', '2026-01-01T00:00:02Z'),
                (
                    2, 20, 'user',
                    'Billing cycle is monthly and invoice is generated on day one.',
                    't4', '2026-01-01T00:00:03Z'
                )
            """
        )


def test_noise_filter_rules():
    assert is_noise_text("hi")
    assert is_noise_text("thanks!")
    assert not is_noise_text("Reset password from settings page and follow email link.")


def test_extract_candidate_lines_removes_noise():
    text = "hello\nReset password via account settings.\nthanks"
    assert extract_candidate_lines(text) == ["Reset password via account settings."]


def test_extract_from_transcripts_new_ids_match_repository_rows(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    _init_transcript_db(transcript_path)

    repository = KnowledgeCandidateRepository(
        db_path=knowledge_path,
        transcript_db_path=transcript_path,
    )
    result = repository.extract_from_transcripts()
    items = repository.list_candidates()
    assert {c.id for c in result.new_candidates} == {item.id for item in items}


def test_extract_from_transcripts_is_idempotent(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    _init_transcript_db(transcript_path)

    repository = KnowledgeCandidateRepository(
        db_path=knowledge_path,
        transcript_db_path=transcript_path,
    )
    first = repository.extract_from_transcripts()
    second = repository.extract_from_transcripts()
    items = repository.list_candidates()
    assert first.inserted == 2
    assert len(first.new_candidates) == 2
    assert second.inserted == 0
    assert len(items) == 2
    assert {item.conversation_id for item in items} == {1, 2}


def test_extract_and_list_for_specific_conversation(tmp_path):
    transcript_path = str(tmp_path / "transcripts.sqlite3")
    knowledge_path = str(tmp_path / "knowledge.sqlite3")
    _init_transcript_db(transcript_path)
    repository = KnowledgeCandidateRepository(
        db_path=knowledge_path,
        transcript_db_path=transcript_path,
    )

    result = repository.extract_from_transcripts(conversation_id=1)
    inserted = result.inserted
    items = repository.list_candidates(conversation_id=1)
    assert inserted == 1
    assert len(items) == 1
    assert items[0].conversation_id == 1
