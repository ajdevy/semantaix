"""Story 12.06 — default ``grounding_system`` prompt carries the FR-25 rule."""

from __future__ import annotations

from services.api.app.project_prompts import (
    ProjectPromptRepository,
    default_prompt,
    resolve_prompt,
)

_FR25_FRAGMENTS = (
    "перечисли только названия",
    "не дампи всё подряд",
)


def _has_fr25_rule(text: str) -> bool:
    lowered = text.lower()
    return all(fragment in lowered for fragment in _FR25_FRAGMENTS)


def test_default_grounding_system_includes_fr25_rule():
    text = default_prompt("grounding_system")
    assert _has_fr25_rule(text), (
        "default grounding_system prompt must include the FR-25 humanistic rule"
    )


def test_project_override_still_takes_precedence(tmp_path):
    repo = ProjectPromptRepository(str(tmp_path / "p.sqlite3"))
    custom = "Custom prompt with {name} on {today_iso} — do whatever."
    repo.set(
        project_id=1,
        prompt_name="grounding_system",
        value=custom,
        edited_by="@admin",
    )
    resolved = resolve_prompt(repo, 1, "grounding_system")
    assert resolved == custom
    # Other projects still see the default with the FR-25 rule.
    other = resolve_prompt(repo, 999, "grounding_system")
    assert _has_fr25_rule(other)
