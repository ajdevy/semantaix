from __future__ import annotations

import pytest

from services.api.app.answer_trace import (
    AnswerTraceRepository,
    _truncate_snippet,
    init_schema,
)


def _build_repository(tmp_path) -> AnswerTraceRepository:
    return AnswerTraceRepository(
        db_path=str(tmp_path / "answer_traces.sqlite3"),
        snippet_max_chars=20,
    )


def test_init_schema_is_idempotent(tmp_path):
    db_path = str(tmp_path / "traces.sqlite3")
    init_schema(db_path)
    init_schema(db_path)


def test_truncate_snippet_short_returns_unchanged():
    assert _truncate_snippet("short", 20) == "short"


def test_truncate_snippet_long_appends_ellipsis():
    text = "x" * 50
    truncated = _truncate_snippet(text, 10)
    assert truncated.endswith("…")
    assert len(truncated) == 11


def test_write_persists_all_fields(tmp_path):
    repository = _build_repository(tmp_path)

    trace = repository.write(
        trace_id="t-1",
        request_text="hi",
        model_id="model-x",
        model_provider="openrouter",
        latency_ms=42,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=["ok"],
        guardrail_score=0.9,
        retrieval=[
            {"chunk_id": "1", "source_ref": "s", "score": 0.5, "text_snippet": "abc"},
        ],
        confidence=0.9,
        limitations=[],
    )

    assert trace.trace_id == "t-1"
    assert trace.guardrails_applied is True
    assert trace.guardrail_reasons == ["ok"]
    assert trace.grounded is True
    assert trace.no_retrieval_hit is False
    assert trace.retrieval[0]["chunk_id"] == "1"


def test_write_marks_no_retrieval_hit_and_not_grounded_without_chunks(tmp_path):
    repository = _build_repository(tmp_path)
    trace = repository.write(
        trace_id="t-empty",
        request_text="hi",
        model_id=None,
        model_provider=None,
        latency_ms=None,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=["partial_context"],
    )
    assert trace.no_retrieval_hit is True
    assert trace.grounded is False
    assert trace.limitations == ["partial_context"]


def test_write_truncates_snippets(tmp_path):
    repository = _build_repository(tmp_path)
    trace = repository.write(
        trace_id="t-trunc",
        request_text="hi",
        model_id=None,
        model_provider=None,
        latency_ms=None,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[
            {
                "chunk_id": "1",
                "source_ref": "s",
                "score": 0.1,
                "text_snippet": "x" * 100,
            }
        ],
        confidence=None,
        limitations=[],
    )
    assert trace.retrieval[0]["text_snippet"].endswith("…")
    assert len(trace.retrieval[0]["text_snippet"]) <= 21


def test_write_is_idempotent_for_same_trace_id(tmp_path):
    repository = _build_repository(tmp_path)
    first = repository.write(
        trace_id="t-id",
        request_text="hi",
        model_id="m",
        model_provider="p",
        latency_ms=10,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=[],
    )
    second = repository.write(
        trace_id="t-id",
        request_text="changed-but-ignored",
        model_id="m2",
        model_provider="p2",
        latency_ms=99,
        response_mode="blocked_invalid",
        guardrails_applied=True,
        guardrail_outcome="invalid",
        guardrail_reasons=["a"],
        guardrail_score=0.1,
        retrieval=[],
        confidence=0.1,
        limitations=["policy_blocked"],
    )
    assert first.id == second.id
    assert second.request_text == "hi"


def test_write_rejects_blank_trace_id(tmp_path):
    repository = _build_repository(tmp_path)
    with pytest.raises(ValueError):
        repository.write(
            trace_id="",
            request_text="hi",
            model_id=None,
            model_provider=None,
            latency_ms=None,
            response_mode="suggestion_only",
            guardrails_applied=True,
            guardrail_outcome="valid",
            guardrail_reasons=[],
            guardrail_score=None,
            retrieval=[],
            confidence=None,
            limitations=[],
        )


def test_write_rejects_blank_response_mode(tmp_path):
    repository = _build_repository(tmp_path)
    with pytest.raises(ValueError):
        repository.write(
            trace_id="t",
            request_text="hi",
            model_id=None,
            model_provider=None,
            latency_ms=None,
            response_mode="",
            guardrails_applied=True,
            guardrail_outcome="valid",
            guardrail_reasons=[],
            guardrail_score=None,
            retrieval=[],
            confidence=None,
            limitations=[],
        )


def test_get_by_trace_id_returns_persisted_record(tmp_path):
    repository = _build_repository(tmp_path)
    repository.write(
        trace_id="t",
        request_text="hi",
        model_id="m",
        model_provider="p",
        latency_ms=10,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=0.5,
        retrieval=[],
        confidence=0.5,
        limitations=[],
    )
    fetched = repository.get_by_trace_id("t")
    assert fetched.trace_id == "t"
    assert fetched.model_id == "m"


def test_get_by_trace_id_missing_raises_lookup(tmp_path):
    repository = _build_repository(tmp_path)
    with pytest.raises(LookupError):
        repository.get_by_trace_id("missing")


def test_list_traces_returns_recent_first(tmp_path):
    repository = _build_repository(tmp_path)
    for i in range(3):
        repository.write(
            trace_id=f"t-{i}",
            request_text="hi",
            model_id=None,
            model_provider=None,
            latency_ms=None,
            response_mode="suggestion_only",
            guardrails_applied=True,
            guardrail_outcome="valid",
            guardrail_reasons=[],
            guardrail_score=None,
            retrieval=[],
            confidence=None,
            limitations=[],
        )
    listed = repository.list_traces(limit=2)
    assert [t.trace_id for t in listed] == ["t-2", "t-1"]


def test_write_persists_hitl_ticket_id(tmp_path):
    """Escalation traces carry the HITL ticket id so an idempotent replay
    of /conversations/inbound can return the original ticket without
    re-creating it."""
    repository = _build_repository(tmp_path)
    trace = repository.write(
        trace_id="t-esc",
        request_text="когда возврат?",
        model_id=None,
        model_provider=None,
        latency_ms=12,
        response_mode="human_only",
        guardrails_applied=True,
        guardrail_outcome="escalated",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=["awaiting_human_response"],
        hitl_ticket_id=42,
    )
    assert trace.hitl_ticket_id == 42
    fetched = repository.get_by_trace_id("t-esc")
    assert fetched.hitl_ticket_id == 42


def test_find_by_trace_id_returns_none_when_missing(tmp_path):
    repository = _build_repository(tmp_path)
    assert repository.find_by_trace_id("missing") is None


def test_find_by_trace_id_returns_trace_when_present(tmp_path):
    repository = _build_repository(tmp_path)
    repository.write(
        trace_id="t-here",
        request_text="hi",
        model_id=None,
        model_provider=None,
        latency_ms=None,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=[],
    )
    fetched = repository.find_by_trace_id("t-here")
    assert fetched is not None
    assert fetched.trace_id == "t-here"


def test_init_schema_alters_legacy_db_to_add_hitl_ticket_id(tmp_path):
    """Production databases predating the idempotency fix lack the
    hitl_ticket_id column. init_schema must add it idempotently so reads
    don't crash after deploy."""
    import sqlite3

    db_path = str(tmp_path / "legacy.sqlite3")
    # Recreate the pre-migration schema by hand.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE answer_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                request_text TEXT NOT NULL,
                model_id TEXT,
                model_provider TEXT,
                latency_ms INTEGER,
                response_mode TEXT NOT NULL,
                guardrails_applied INTEGER NOT NULL,
                guardrail_outcome TEXT NOT NULL,
                guardrail_reasons TEXT NOT NULL,
                guardrail_score REAL,
                grounded INTEGER NOT NULL,
                no_retrieval_hit INTEGER NOT NULL,
                confidence REAL,
                retrieval_json TEXT NOT NULL,
                limitations_json TEXT NOT NULL
            )
            """
        )

    # init_schema must add the column without raising.
    init_schema(db_path)
    init_schema(db_path)  # second call is a no-op
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(answer_traces)").fetchall()
        }
        assert "hitl_ticket_id" in columns


def test_list_traces_with_zero_or_negative_limit(tmp_path):
    repository = _build_repository(tmp_path)
    repository.write(
        trace_id="t-only",
        request_text="hi",
        model_id=None,
        model_provider=None,
        latency_ms=None,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=[],
    )
    assert repository.list_traces(limit=0) == []
    assert repository.list_traces(limit=-3) == []
