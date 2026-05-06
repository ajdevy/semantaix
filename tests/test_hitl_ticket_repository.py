from services.api.app.hitl import HitlTicketRepository


def test_hitl_ticket_lifecycle(tmp_path):
    repository = HitlTicketRepository(str(tmp_path / "hitl.sqlite3"))

    created = repository.create(conversation_ref="conv-1", reason="low_confidence")
    assert created.status == "open"
    assert created.operator_username is None

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
