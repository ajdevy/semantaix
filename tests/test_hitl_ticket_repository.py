import sqlite3

from services.api.app.hitl import HitlTicketRepository


def test_hitl_ticket_lifecycle(tmp_path):
    repository = HitlTicketRepository(str(tmp_path / "hitl.sqlite3"))

    created = repository.create(conversation_ref="conv-1", reason="low_confidence")
    assert created.status == "open"
    assert created.operator_username is None
    assert created.target_chat_id is None

    assigned = repository.assign(ticket_id=created.id, operator_username="@ajdevy")
    assert assigned.status == "assigned"
    assert assigned.operator_username == "@ajdevy"

    resolved = repository.resolve(ticket_id=created.id)
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None

    items = repository.list_all()
    assert len(items) == 1
    assert items[0].id == created.id
    fetched = repository.get(created.id)
    assert fetched.id == created.id


def test_hitl_ticket_stores_target_chat_id(tmp_path):
    repository = HitlTicketRepository(str(tmp_path / "hitl.sqlite3"))
    created = repository.create(
        conversation_ref="conv-7",
        reason="uncertain",
        target_chat_id=123456,
    )
    assert created.target_chat_id == 123456


def test_hitl_schema_migration_adds_target_chat_id(tmp_path):
    db_path = tmp_path / "legacy_hitl.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE hitl_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_ref TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            operator_username TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    repository = HitlTicketRepository(str(db_path))
    created = repository.create(conversation_ref="conv-legacy", reason="migration")
    assert created.target_chat_id is None
