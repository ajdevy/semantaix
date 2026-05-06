import sqlite3

from services.bot_gateway.app.persistence import TelegramConversationRepository


def test_create_or_get_conversation_reuses_same_row(tmp_path):
    db_path = tmp_path / "repo.sqlite3"
    repository = TelegramConversationRepository(str(db_path))

    first_id = repository.create_or_get_conversation(telegram_user_id=9001)
    second_id = repository.create_or_get_conversation(telegram_user_id=9001)

    assert first_id == second_id


def test_append_message_if_new_is_idempotent_and_persists_trace(tmp_path):
    db_path = tmp_path / "repo.sqlite3"
    repository = TelegramConversationRepository(str(db_path))
    conversation_id = repository.create_or_get_conversation(telegram_user_id=9001)

    first = repository.append_message_if_new(
        conversation_id=conversation_id,
        source_message_id=501,
        role="user",
        text="Hello",
        trace_id="trace-abc",
    )
    second = repository.append_message_if_new(
        conversation_id=conversation_id,
        source_message_id=501,
        role="user",
        text="Hello again",
        trace_id="trace-def",
    )

    assert first is True
    assert second is False

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT source_message_id, role, text, trace_id FROM messages"
        ).fetchone()
        assert row == (501, "user", "Hello", "trace-abc")
