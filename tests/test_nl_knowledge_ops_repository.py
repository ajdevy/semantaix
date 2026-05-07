from __future__ import annotations

import pytest

from services.api.app.nl_knowledge_ops import (
    NlKnowledgeOpsError,
    NlKnowledgeOpsRepository,
    init_schema,
    parse_intent,
)


def _build(tmp_path) -> NlKnowledgeOpsRepository:
    return NlKnowledgeOpsRepository(db_path=str(tmp_path / "nl_ops.sqlite3"))


def test_init_schema_idempotent(tmp_path):
    db = str(tmp_path / "nl_ops.sqlite3")
    init_schema(db)
    init_schema(db)


@pytest.mark.parametrize(
    "utterance, expected_intent, expected_draft",
    [
        ("add reset link policy text", "create", "reset link policy text"),
        ("create new entry", "create", "new entry"),
        ("update billing info", "update", "billing info"),
        ("change pricing tier", "update", "pricing tier"),
        ("delete deprecated rule", "deprecate", "deprecated rule"),
        ("retire old policy", "deprecate", "old policy"),
        ("hello", "clarify", "hello"),
        ("", "clarify", ""),
    ],
)
def test_parse_intent_table(utterance, expected_intent, expected_draft):
    intent, draft = parse_intent(utterance)
    assert intent == expected_intent
    assert draft == expected_draft


def test_propose_creates_pending_session_for_create(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(
        tenant_id="t1",
        user_id="u1",
        utterance="add reset password policy",
    )
    assert session.intent == "create"
    assert session.status == "pending_confirmation"
    assert session.confirm_token is not None
    audits = repo.list_audit_logs(tenant_id="t1")
    assert audits[0].op_type == "preview_created"


def test_propose_clarify_when_intent_unrecognized(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="please help me")
    assert session.intent == "clarify"
    assert session.status == "clarify"
    assert session.confirm_token is None
    audits = repo.list_audit_logs()
    assert audits[0].op_type == "clarify_requested"


def test_propose_clarify_when_mutating_body_blank(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add ")
    assert session.intent == "clarify"
    assert session.status == "clarify"
    assert session.confirm_token is None


def test_propose_validates_required_fields(tmp_path):
    repo = _build(tmp_path)
    with pytest.raises(NlKnowledgeOpsError):
        repo.propose(tenant_id="", user_id="u1", utterance="hi")
    with pytest.raises(NlKnowledgeOpsError):
        repo.propose(tenant_id="t", user_id="", utterance="hi")
    with pytest.raises(NlKnowledgeOpsError):
        repo.propose(tenant_id="t", user_id="u", utterance="   ")


def test_confirm_publishes_version_and_audit(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add policy text")
    confirmed, version = repo.confirm(
        session_id=session.id,
        confirm_token=session.confirm_token,
    )
    assert confirmed.status == "confirmed"
    assert confirmed.knowledge_version_id == version.id
    assert version.tenant_id == "t1"
    assert version.version_number == 1
    assert version.status == "published"
    assert version.source_id.endswith(f":{session.id}")
    audits = [log.op_type for log in repo.list_audit_logs(tenant_id="t1")]
    assert "confirmed" in audits


def test_confirm_marks_deprecated_for_deprecate_intent(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="delete old rule")
    _, version = repo.confirm(
        session_id=session.id,
        confirm_token=session.confirm_token,
    )
    assert version.status == "deprecated"


def test_confirm_increments_version_per_tenant(tmp_path):
    repo = _build(tmp_path)
    s1 = repo.propose(tenant_id="t1", user_id="u1", utterance="add a")
    repo.confirm(session_id=s1.id, confirm_token=s1.confirm_token)
    s2 = repo.propose(tenant_id="t1", user_id="u1", utterance="add b")
    _, v2 = repo.confirm(session_id=s2.id, confirm_token=s2.confirm_token)
    s3 = repo.propose(tenant_id="t2", user_id="u1", utterance="add c")
    _, v3 = repo.confirm(session_id=s3.id, confirm_token=s3.confirm_token)
    assert v2.version_number == 2
    assert v3.version_number == 1


def test_confirm_rejects_invalid_token(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add policy")
    with pytest.raises(NlKnowledgeOpsError) as exc:
        repo.confirm(session_id=session.id, confirm_token="wrong")
    assert "invalid_confirm_token" in str(exc.value)
    audits = [log.op_type for log in repo.list_audit_logs()]
    assert "confirm_rejected" in audits


def test_confirm_rejects_already_confirmed(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add policy")
    repo.confirm(session_id=session.id, confirm_token=session.confirm_token)
    with pytest.raises(NlKnowledgeOpsError) as exc:
        repo.confirm(session_id=session.id, confirm_token=session.confirm_token)
    assert "already_confirmed" in str(exc.value)


def test_confirm_rejects_clarify_status(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="hello there")
    with pytest.raises(NlKnowledgeOpsError) as exc:
        repo.confirm(session_id=session.id, confirm_token="anything")
    assert "invalid_status" in str(exc.value)


def test_cancel_transitions_to_cancelled(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add policy")
    cancelled = repo.cancel(session_id=session.id)
    assert cancelled.status == "cancelled"
    assert cancelled.confirm_token is None
    audits = [log.op_type for log in repo.list_audit_logs()]
    assert "cancelled" in audits


def test_cancel_after_confirm_raises(tmp_path):
    repo = _build(tmp_path)
    session = repo.propose(tenant_id="t1", user_id="u1", utterance="add policy")
    repo.confirm(session_id=session.id, confirm_token=session.confirm_token)
    with pytest.raises(NlKnowledgeOpsError):
        repo.cancel(session_id=session.id)


def test_get_session_missing_raises(tmp_path):
    repo = _build(tmp_path)
    with pytest.raises(LookupError):
        repo.get_session(999)


def test_list_sessions_by_tenant(tmp_path):
    repo = _build(tmp_path)
    repo.propose(tenant_id="t1", user_id="u", utterance="add a")
    repo.propose(tenant_id="t2", user_id="u", utterance="add b")
    only_t1 = repo.list_sessions(tenant_id="t1")
    all_sessions = repo.list_sessions()
    assert len(only_t1) == 1
    assert len(all_sessions) == 2


def test_list_versions_filter(tmp_path):
    repo = _build(tmp_path)
    s1 = repo.propose(tenant_id="t1", user_id="u", utterance="add a")
    repo.confirm(session_id=s1.id, confirm_token=s1.confirm_token)
    s2 = repo.propose(tenant_id="t2", user_id="u", utterance="add b")
    repo.confirm(session_id=s2.id, confirm_token=s2.confirm_token)
    only_t1 = repo.list_versions(tenant_id="t1")
    all_versions = repo.list_versions()
    assert len(only_t1) == 1
    assert len(all_versions) == 2


def test_list_audit_logs_filter(tmp_path):
    repo = _build(tmp_path)
    repo.propose(tenant_id="t1", user_id="u", utterance="add a")
    repo.propose(tenant_id="t2", user_id="u", utterance="add b")
    only_t1 = repo.list_audit_logs(tenant_id="t1")
    all_logs = repo.list_audit_logs()
    assert all(log.tenant_id == "t1" for log in only_t1)
    assert len(all_logs) >= len(only_t1)
