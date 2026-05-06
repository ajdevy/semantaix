from services.api.app.rag import RagRepository, split_into_chunks


def test_split_into_chunks_removes_empty_lines():
    chunks = split_into_chunks("Hello\n\nWorld\n")
    assert chunks == ["Hello", "World"]


def test_split_into_chunks_returns_empty_for_blank_text():
    assert split_into_chunks(" \n \n") == []


def test_ingest_is_idempotent(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    first = repository.ingest(source_id="doc-1", text="line one\nline two")
    second = repository.ingest(source_id="doc-1", text="line one\nline two")
    assert first == 2
    assert second == 0


def test_retrieve_scores_and_limits(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-1",
        text="reset password via email link\nbilling cycle is monthly",
    )
    repository.ingest(source_id="kb-2", text="password reset requires account email")
    items = repository.retrieve(query="password reset", limit=1)
    assert len(items) == 1
    assert items[0].source_id in {"kb-1", "kb-2"}
    assert items[0].score > 0
