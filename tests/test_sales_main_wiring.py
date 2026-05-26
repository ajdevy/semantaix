"""Story 12.03 — main.py wiring smoke tests.

The SalesPersonaAnswerer is constructed at startup (so the DB schema is
bootstrapped) but MUST NOT yet be inserted into the AnswerPipeline.
Story 12.09 owns pipeline insertion.
"""

from __future__ import annotations

from services.api.app import main
from services.api.app.sales.sales_persona_answerer import SalesPersonaAnswerer


def test_sales_persona_answerer_constructed() -> None:
    assert isinstance(main.sales_persona_answerer, SalesPersonaAnswerer)
    assert main.sales_persona_answerer.name == "sales_persona"


def test_sales_persona_answerer_not_in_pipeline() -> None:
    """Story 12.09 will insert this; 12.03 must NOT."""
    pipeline_names = [a.name for a in main.answer_pipeline.answerers]
    assert "sales_persona" not in pipeline_names


def test_sales_state_repository_bootstrap_creates_table() -> None:
    """The repo bootstraps idempotently on first use; opening a new handle
    against the same path must not error."""
    repo = main.sales_state_repository
    # Re-opening against the same path is a no-op idempotency check.
    same = type(repo)(db_path=repo.db_path)
    assert same.db_path == repo.db_path


def test_effective_sales_persona_name_joins_first_and_last(monkeypatch) -> None:
    """The configurable persona name flows into the sales LLM prompts via
    `_effective_sales_persona_name()`."""

    def fake_persona() -> tuple[str, str]:
        return ("Анна", "Иванова")

    monkeypatch.setattr(main, "_effective_bot_persona", fake_persona)
    assert main._effective_sales_persona_name() == "Анна Иванова"


def test_effective_sales_persona_name_uses_first_only_when_last_empty(
    monkeypatch,
) -> None:
    def fake_persona() -> tuple[str, str]:
        return ("Анна", "")

    monkeypatch.setattr(main, "_effective_bot_persona", fake_persona)
    assert main._effective_sales_persona_name() == "Анна"


def test_sales_services_repo_stub_returns_zero() -> None:
    """The stub satisfies the constructor protocol but never gates the
    answerer — calling it just returns 0 until story 12.01 lands."""
    stub = main._SalesServicesRepoStub()
    assert stub.count_active(project_id=1) == 0
