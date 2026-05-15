import pytest

from services.api.app.knowledge_moderation import KnowledgeModerationRepository


def test_set_project_id_updates_existing_row(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "km.sqlite3"))
    candidate = repository.create_pending(
        text="Long enough candidate text for moderation flow."
    )
    repository.set_project_id(candidate_id=candidate.id, project_id=42)
    fetched = repository.get(candidate.id)
    assert fetched.project_id == 42


def test_set_project_id_unknown_candidate_raises(tmp_path):
    repository = KnowledgeModerationRepository(str(tmp_path / "km.sqlite3"))
    with pytest.raises(LookupError):
        repository.set_project_id(candidate_id=9999, project_id=1)
