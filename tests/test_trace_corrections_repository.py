from __future__ import annotations

import pytest

from services.api.app.trace_corrections import (
    TraceCorrectionError,
    TraceCorrectionRepository,
    init_schema,
)


def _build(tmp_path) -> TraceCorrectionRepository:
    return TraceCorrectionRepository(db_path=str(tmp_path / "trace_corrections.sqlite3"))


def test_init_schema_idempotent(tmp_path):
    db = str(tmp_path / "tc.sqlite3")
    init_schema(db)
    init_schema(db)


def test_record_open_writes_audit(tmp_path):
    repo = _build(tmp_path)
    repo.record_open(trace_id="t-1", tenant_id="org", user_id="u")
    audit = repo.list_audit()
    assert audit[0]["op_type"] == "trace_opened"
    assert audit[0]["details"]["trace_id"] == "t-1"


def test_record_open_validates_inputs(tmp_path):
    repo = _build(tmp_path)
    with pytest.raises(TraceCorrectionError):
        repo.record_open(trace_id="", tenant_id="o", user_id="u")
    with pytest.raises(TraceCorrectionError):
        repo.record_open(trace_id="t", tenant_id="", user_id="u")
    with pytest.raises(TraceCorrectionError):
        repo.record_open(trace_id="t", tenant_id="o", user_id="")


def test_submit_publish_creates_row_and_audit(tmp_path):
    repo = _build(tmp_path)
    correction = repo.submit_publish(
        trace_id="t-1", tenant_id="org", user_id="u", edited_text="new policy"
    )
    assert correction.branch == "publish"
    assert correction.status == "published"
    assert correction.source_id == "trace_correction:org:t-1"
    audit = repo.list_audit(trace_id="t-1")
    op_types = {entry["op_type"] for entry in audit}
    assert "correction_published" in op_types


def test_submit_moderation_links_candidate(tmp_path):
    repo = _build(tmp_path)
    correction = repo.submit_moderation(
        trace_id="t-1",
        tenant_id="org",
        user_id="u",
        edited_text="new policy",
        candidate_id=42,
    )
    assert correction.branch == "moderation"
    assert correction.status == "pending_moderation"
    assert correction.candidate_id == 42
    assert correction.source_id is None
    audit = repo.list_audit(trace_id="t-1")
    op_types = {entry["op_type"] for entry in audit}
    assert "correction_pending_moderation" in op_types


def test_submit_validates_required_fields(tmp_path):
    repo = _build(tmp_path)
    with pytest.raises(TraceCorrectionError):
        repo.submit_publish(trace_id="", tenant_id="o", user_id="u", edited_text="x")
    with pytest.raises(TraceCorrectionError):
        repo.submit_publish(trace_id="t", tenant_id="o", user_id="u", edited_text="   ")


def test_list_for_trace_filters(tmp_path):
    repo = _build(tmp_path)
    repo.submit_publish(trace_id="t-1", tenant_id="o", user_id="u", edited_text="a")
    repo.submit_publish(trace_id="t-2", tenant_id="o", user_id="u", edited_text="b")
    only_t1 = repo.list_for_trace("t-1")
    assert len(only_t1) == 1
    assert only_t1[0].trace_id == "t-1"


def test_list_audit_returns_all_when_no_trace_filter(tmp_path):
    repo = _build(tmp_path)
    repo.record_open(trace_id="t", tenant_id="o", user_id="u")
    repo.submit_publish(trace_id="t", tenant_id="o", user_id="u", edited_text="x")
    all_logs = repo.list_audit()
    assert len(all_logs) >= 2
