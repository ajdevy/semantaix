from __future__ import annotations

from services.api.app.rag import RagRepository


def test_ingest_preserves_is_confidential_flag(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.db"))
    inserted = repo.ingest(
        source_id="knowledge_candidate:1",
        text="Конфиденциальные данные офиса.\nВторая строка с цифрами 1234567890.",
        is_confidential=True,
    )
    assert inserted == 2
    chunks = repo.retrieve(query="конфиденциальные данные офиса", limit=3)
    assert chunks
    assert all(chunk.is_confidential is True for chunk in chunks)


def test_default_ingest_marks_non_confidential(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.db"))
    repo.ingest(source_id="src", text="Открытая информация для всех.")
    chunks = repo.retrieve(query="открытая информация всех", limit=3)
    assert chunks
    assert chunks[0].is_confidential is False


def test_retrieve_returns_score_and_flag(tmp_path):
    repo = RagRepository(str(tmp_path / "rag.db"))
    repo.ingest(
        source_id="public",
        text="часы работы офиса утром",
        is_confidential=False,
    )
    repo.ingest(
        source_id="confidential",
        text="часы работы офиса вечером",
        is_confidential=True,
    )
    chunks = repo.retrieve(query="часы работы офиса", limit=5)
    flags = {chunk.source_id: chunk.is_confidential for chunk in chunks}
    assert flags["public"] is False
    assert flags["confidential"] is True
