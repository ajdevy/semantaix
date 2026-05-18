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


def test_retrieve_matches_russian_inflection_via_lemma(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-ru",
        text="Возврат денег занимает пять рабочих дней",
    )
    items = repository.retrieve(query="когда придут деньги", limit=1)
    assert len(items) == 1
    assert items[0].source_id == "kb-ru"
    assert items[0].score > 0


def test_retrieve_matches_russian_slang_via_normalization(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-money",
        text="Возврат денег занимает пять рабочих дней",
    )
    # "бабло" should be slang-substituted to "деньги", then lemma-matched.
    items = repository.retrieve(query="когда придёт бабло", limit=1)
    assert len(items) == 1
    assert items[0].source_id == "kb-money"


def test_retrieve_buggy_tour_natural_language_query(tmp_path):
    """Regression: an intent-laden short query like "хочу поехать на багги тур"
    must score above the default grounding threshold (0.6) against a catalog
    chunk that mentions only the content nouns ("багги", "тур"). The intent /
    connector lemmas ("хотеть", "поехать", "на") are filtered from the scoring
    denominator so they don't deflate the overlap ratio.
    """
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-buggy",
        text="Багги-тур по дюнам. Ежедневно в 9:00. Стоимость 2500 руб.",
    )
    items = repository.retrieve(query="хочу поехать на багги тур", limit=1)
    assert len(items) == 1
    assert items[0].source_id == "kb-buggy"
    assert items[0].score >= 0.6
    assert items[0].score <= 1.0


def test_retrieve_score_uses_content_tokens_denominator(tmp_path):
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-buggy",
        text="Багги-тур по дюнам. Стоимость 2500 руб.",
    )
    # Content-only query — both lemmas match the chunk, score must equal 1.0.
    items = repository.retrieve(query="багги тур", limit=1)
    assert len(items) == 1
    assert items[0].score == 1.0


def test_retrieve_stopword_only_query_falls_back(tmp_path):
    """A query made entirely of stop lemmas (e.g. "что? как?") must not award
    a perfect score against an arbitrary chunk. The scorer falls back to the
    full token set so the denominator stays non-zero and overlap stays partial.
    """
    repository = RagRepository(str(tmp_path / "rag.sqlite3"))
    repository.ingest(
        source_id="kb-help",
        text="Возврат денег занимает пять рабочих дней",
    )
    items = repository.retrieve(query="что как где", limit=3)
    # Either no chunks (zero overlap) or low score — never a free 1.0.
    for item in items:
        assert item.score < 1.0
