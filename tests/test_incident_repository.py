import sqlite3
import time

from services.api.app.incidents import IncidentRepository, init_schema


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


def test_init_schema_migrates_legacy_incidents_table(tmp_path):
    db_path = tmp_path / "incidents.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                severity TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )

    init_schema(str(db_path))

    with sqlite3.connect(db_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(incidents)").fetchall()]
        assert "is_read" in columns
        assert "acknowledged_at" in columns
        assert "resolved_at" in columns
