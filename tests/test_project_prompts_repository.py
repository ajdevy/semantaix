from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from services.api.app import project_prompts
from services.api.app.project_prompts import (
    MAX_PROMPT_VALUE_BYTES,
    PROMPT_NAME_LIST,
    PROMPT_NAMES,
    PendingEdit,
    ProjectPromptRepository,
    PromptCurrent,
    PromptValueInvalid,
    PromptValueTooLarge,
    PromptVersion,
    PromptVersionNotFound,
    UnknownPromptName,
    default_prompt,
    normalize_value,
    resolve_prompt,
    split_guardrail_lines,
    validate_value,
)


def _repo(tmp_path) -> ProjectPromptRepository:
    return ProjectPromptRepository(str(tmp_path / "prompts.sqlite3"))


def test_prompt_names_constants_are_consistent():
    assert PROMPT_NAMES == frozenset(PROMPT_NAME_LIST)
    assert len(PROMPT_NAME_LIST) == 7


def test_init_schema_is_idempotent(tmp_path):
    path = str(tmp_path / "prompts.sqlite3")
    repository = ProjectPromptRepository(path)
    repository.init_schema()
    repository.init_schema()
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {row[0] for row in rows}
    assert {
        "project_prompts",
        "project_prompt_versions",
        "pending_prompt_edits",
    }.issubset(names)


def test_default_prompt_for_grounding_returns_constant():
    text = default_prompt("grounding_system")
    assert "{name}" in text and "{today_iso}" in text


def test_default_prompt_for_verifier_returns_constant():
    text = default_prompt("verifier_system")
    assert "GROUNDED" in text


def test_default_prompt_for_catalog_digest_returns_constant():
    text = default_prompt("catalog_digest_system")
    assert isinstance(text, str)
    assert "NO_OFFERINGS" in text


def test_default_prompt_for_inbound_ack_returns_settings_value():
    text = default_prompt("inbound_ack")
    assert isinstance(text, str)
    assert text  # non-empty


def test_default_prompt_for_each_guardrail_returns_file_contents():
    for name in ("guardrail_hedges", "guardrail_policy", "guardrail_profanity"):
        text = default_prompt(name)
        assert isinstance(text, str)
        assert text.strip()  # files are non-empty


def test_default_prompt_unknown_name_raises():
    with pytest.raises(UnknownPromptName):
        default_prompt("not_a_real_prompt")


def test_validate_value_unknown_name_raises():
    with pytest.raises(UnknownPromptName):
        validate_value("nope", "x")


def test_validate_value_empty_raises():
    with pytest.raises(PromptValueInvalid):
        validate_value("verifier_system", "")


def test_validate_value_too_large_raises():
    big = "x" * (MAX_PROMPT_VALUE_BYTES + 1)
    with pytest.raises(PromptValueTooLarge):
        validate_value("verifier_system", big)


def test_validate_value_grounding_missing_name_placeholder_raises():
    with pytest.raises(PromptValueInvalid):
        validate_value(
            "grounding_system", "только today_iso={today_iso}, без name"
        )


def test_validate_value_grounding_missing_today_iso_placeholder_raises():
    with pytest.raises(PromptValueInvalid):
        validate_value(
            "grounding_system", "только {name}, без today_iso"
        )


def test_validate_value_grounding_valid_passes():
    validate_value(
        "grounding_system", "Hello {name}, today is {today_iso}"
    )


def test_validate_value_other_names_skip_placeholder_check():
    # Non-grounding prompts have no placeholder requirement.
    validate_value("verifier_system", "anything works")
    validate_value("inbound_ack", "x")
    validate_value("guardrail_hedges", "x")


def test_normalize_value_unknown_name_raises():
    with pytest.raises(UnknownPromptName):
        normalize_value("nope", "x")


def test_normalize_value_guardrail_strips_and_drops_blank_lines():
    raw = "  alpha  \n\n  beta\n\n\ngamma   \n"
    assert normalize_value("guardrail_hedges", raw) == "alpha\nbeta\ngamma"


def test_normalize_value_non_guardrail_returns_verbatim():
    raw = "  spaces   preserved \n line two "
    assert normalize_value("verifier_system", raw) == raw


def test_split_guardrail_lines_strips_and_drops_blanks():
    assert split_guardrail_lines("  a \n\n b \n\nc  \n") == ["a", "b", "c"]


def test_split_guardrail_lines_drops_comments():
    raw = "# header comment\nалfa\n# inline note\nбета\n"
    assert split_guardrail_lines(raw) == ["алfa", "бета"]


def test_get_unknown_name_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(UnknownPromptName):
        repo.get(project_id=1, prompt_name="bogus")


def test_get_returns_none_when_missing(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get(project_id=1, prompt_name="verifier_system") is None


def test_get_current_returns_none_when_missing(tmp_path):
    repo = _repo(tmp_path)
    assert (
        repo.get_current(project_id=1, prompt_name="verifier_system") is None
    )


def test_get_current_unknown_name_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(UnknownPromptName):
        repo.get_current(project_id=1, prompt_name="bogus")


def test_set_first_value_starts_at_version_one(tmp_path):
    repo = _repo(tmp_path)
    version = repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="My verifier instructions",
        edited_by="@admin",
    )
    assert version == 1
    current = repo.get_current(project_id=1, prompt_name="verifier_system")
    assert isinstance(current, PromptCurrent)
    assert current.version == 1
    assert current.value == "My verifier instructions"
    assert current.updated_by == "@admin"


def test_set_subsequent_writes_bump_version(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="v1",
        edited_by="@a",
    )
    v2 = repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="v2",
        edited_by="@b",
    )
    v3 = repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="v3",
        edited_by="@c",
    )
    assert (v2, v3) == (2, 3)
    versions = repo.list_versions(project_id=1, prompt_name="verifier_system")
    assert [pv.version for pv in versions] == [3, 2, 1]


def test_set_validates_invalid_grounding_value(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(PromptValueInvalid):
        repo.set(
            project_id=1,
            prompt_name="grounding_system",
            value="missing placeholders",
            edited_by="@a",
        )


def test_set_validates_size(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(PromptValueTooLarge):
        repo.set(
            project_id=1,
            prompt_name="verifier_system",
            value="x" * (MAX_PROMPT_VALUE_BYTES + 1),
            edited_by="@a",
        )


def test_set_normalizes_guardrail_lists(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="guardrail_hedges",
        value="  alpha  \n\n  beta\n",
        edited_by="@a",
    )
    assert repo.get(project_id=1, prompt_name="guardrail_hedges") == "alpha\nbeta"


def test_list_current_empty_when_no_rows(tmp_path):
    assert _repo(tmp_path).list_current(1) == []


def test_list_current_returns_all_for_project_sorted(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="v",
        edited_by="@a",
    )
    repo.set(
        project_id=1,
        prompt_name="inbound_ack",
        value="a",
        edited_by="@a",
    )
    # A different project's rows must not leak in.
    repo.set(
        project_id=2,
        prompt_name="verifier_system",
        value="other",
        edited_by="@a",
    )
    listing = repo.list_current(1)
    assert [pc.prompt_name for pc in listing] == [
        "inbound_ack",
        "verifier_system",
    ]


def test_list_versions_unknown_name_raises(tmp_path):
    with pytest.raises(UnknownPromptName):
        _repo(tmp_path).list_versions(project_id=1, prompt_name="bogus")


def test_list_versions_empty_when_no_rows(tmp_path):
    repo = _repo(tmp_path)
    assert (
        repo.list_versions(project_id=1, prompt_name="verifier_system") == []
    )


def test_list_versions_respects_limit(tmp_path):
    repo = _repo(tmp_path)
    for i in range(5):
        repo.set(
            project_id=1,
            prompt_name="verifier_system",
            value=f"v{i}",
            edited_by="@a",
        )
    rows = repo.list_versions(
        project_id=1, prompt_name="verifier_system", limit=2
    )
    assert [pv.version for pv in rows] == [5, 4]


def test_get_version_unknown_name_raises(tmp_path):
    with pytest.raises(UnknownPromptName):
        _repo(tmp_path).get_version(project_id=1, prompt_name="bogus", version=1)


def test_get_version_returns_none_when_missing(tmp_path):
    repo = _repo(tmp_path)
    assert (
        repo.get_version(
            project_id=1, prompt_name="verifier_system", version=99
        )
        is None
    )


def test_get_version_returns_pv(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="hello",
        edited_by="@a",
    )
    pv = repo.get_version(
        project_id=1, prompt_name="verifier_system", version=1
    )
    assert isinstance(pv, PromptVersion)
    assert pv.value == "hello"


def test_restore_unknown_version_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(PromptVersionNotFound):
        repo.restore(
            project_id=1,
            prompt_name="verifier_system",
            version=42,
            edited_by="@a",
        )


def test_restore_creates_new_version_with_old_value(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="original",
        edited_by="@a",
    )
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="changed",
        edited_by="@b",
    )
    restored_version = repo.restore(
        project_id=1,
        prompt_name="verifier_system",
        version=1,
        edited_by="@c",
    )
    assert restored_version == 3
    assert repo.get(project_id=1, prompt_name="verifier_system") == "original"


def test_arm_pending_unknown_name_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(UnknownPromptName):
        repo.arm_pending(
            user_username="@alice", project_id=1, prompt_name="bogus"
        )


def test_arm_pending_creates_row(tmp_path):
    repo = _repo(tmp_path)
    pending = repo.arm_pending(
        user_username="@Alice  ",
        project_id=7,
        prompt_name="verifier_system",
    )
    assert isinstance(pending, PendingEdit)
    assert pending.user_username == "alice"
    assert pending.project_id == 7
    assert pending.prompt_name == "verifier_system"


def test_arm_pending_replaces_existing(tmp_path):
    repo = _repo(tmp_path)
    repo.arm_pending(
        user_username="@alice",
        project_id=1,
        prompt_name="verifier_system",
    )
    repo.arm_pending(
        user_username="@alice",
        project_id=2,
        prompt_name="inbound_ack",
    )
    pending = repo.peek_pending("@alice")
    assert pending is not None
    assert pending.project_id == 2
    assert pending.prompt_name == "inbound_ack"


def test_peek_pending_returns_none_when_absent(tmp_path):
    assert _repo(tmp_path).peek_pending("ghost") is None


def test_peek_pending_drops_expired_rows(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    long_ago = datetime.now(UTC) - timedelta(days=1)
    monkeypatch.setattr(project_prompts, "_now", lambda: long_ago)
    repo.arm_pending(
        user_username="@alice",
        project_id=1,
        prompt_name="verifier_system",
    )
    monkeypatch.undo()

    assert repo.peek_pending("@alice") is None
    # And the row should now be gone.
    with sqlite3.connect(repo.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM pending_prompt_edits"
        ).fetchone()[0]
    assert count == 0


def test_consume_pending_returns_none_when_absent(tmp_path):
    assert _repo(tmp_path).consume_pending("ghost") is None


def test_consume_pending_returns_and_deletes(tmp_path):
    repo = _repo(tmp_path)
    repo.arm_pending(
        user_username="@alice",
        project_id=1,
        prompt_name="verifier_system",
    )
    pending = repo.consume_pending("@alice")
    assert pending is not None
    assert pending.prompt_name == "verifier_system"
    assert repo.peek_pending("@alice") is None


def test_cancel_pending_returns_false_when_absent(tmp_path):
    assert _repo(tmp_path).cancel_pending(user_username="@ghost") is False


def test_cancel_pending_returns_true_when_deleted(tmp_path):
    repo = _repo(tmp_path)
    repo.arm_pending(
        user_username="@alice",
        project_id=1,
        prompt_name="verifier_system",
    )
    assert repo.cancel_pending(user_username="@alice") is True
    assert repo.peek_pending("@alice") is None


def test_normalize_username_lowercases_and_strips_at(tmp_path):
    repo = _repo(tmp_path)
    repo.arm_pending(
        user_username="  @MIXED-Case ",
        project_id=1,
        prompt_name="verifier_system",
    )
    assert repo.peek_pending("mixed-case") is not None


def test_resolve_prompt_returns_default_when_project_id_none(tmp_path):
    repo = _repo(tmp_path)
    text = resolve_prompt(repo, None, "verifier_system")
    assert text == default_prompt("verifier_system")


def test_resolve_prompt_returns_default_when_no_override(tmp_path):
    repo = _repo(tmp_path)
    text = resolve_prompt(repo, 99, "verifier_system")
    assert text == default_prompt("verifier_system")


def test_resolve_prompt_returns_override_when_present(tmp_path):
    repo = _repo(tmp_path)
    repo.set(
        project_id=1,
        prompt_name="verifier_system",
        value="custom verifier",
        edited_by="@a",
    )
    assert resolve_prompt(repo, 1, "verifier_system") == "custom verifier"
