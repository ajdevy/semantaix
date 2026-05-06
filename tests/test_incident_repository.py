import time

from services.api.app.incidents import IncidentRepository


def test_dedup_window_collapses_repeated_events(tmp_path):
    repository = IncidentRepository(
        db_path=str(tmp_path / "incidents.sqlite3"),
        dedup_window_seconds=120,
    )

    first = repository.ingest(
        fingerprint="provider429_spike",
        severity="critical",
        summary="Provider 429 spike detected",
    )
    second = repository.ingest(
        fingerprint="provider429_spike",
        severity="critical",
        summary="Provider 429 spike detected",
    )

    assert first.id == second.id
    assert second.occurrence_count == 2
    incidents = repository.get_by_fingerprint("provider429_spike")
    assert len(incidents) == 1
    assert incidents[0].status == "open"
    assert incidents[0].is_read is False
    timeline = repository.get_timeline(first.id)
    assert [event.event_type for event in timeline] == ["created", "deduplicated"]


def test_event_outside_window_creates_new_incident(tmp_path):
    repository = IncidentRepository(
        db_path=str(tmp_path / "incidents.sqlite3"),
        dedup_window_seconds=0,
    )

    first = repository.ingest(
        fingerprint="db_down",
        severity="critical",
        summary="Database unavailable",
    )
    time.sleep(0.01)
    second = repository.ingest(
        fingerprint="db_down",
        severity="critical",
        summary="Database unavailable",
    )

    assert first.id != second.id
    incidents = repository.get_by_fingerprint("db_down")
    assert len(incidents) == 2
    assert incidents[0].status == "resolved"
    assert incidents[1].status == "open"
    assert incidents[0].resolved_at is not None


def test_read_ack_resolve_transitions_are_persisted(tmp_path):
    repository = IncidentRepository(
        db_path=str(tmp_path / "incidents.sqlite3"),
        dedup_window_seconds=300,
    )
    created = repository.ingest(
        fingerprint="provider5xx_spike",
        severity="critical",
        summary="Provider 5xx spike detected",
    )

    read = repository.mark_read(created.id)
    acknowledged = repository.acknowledge(created.id)
    resolved = repository.resolve(created.id)

    assert read.is_read is True
    assert acknowledged.acknowledged_at is not None
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    timeline = repository.get_timeline(created.id)
    assert [event.event_type for event in timeline] == [
        "created",
        "read",
        "acknowledged",
        "resolved",
    ]
