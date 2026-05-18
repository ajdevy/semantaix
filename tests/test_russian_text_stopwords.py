from __future__ import annotations

from pathlib import Path

from services.api.app.russian_text import (
    get_retrieval_stopwords,
    load_retrieval_stopwords,
)
from services.api.app.russian_text.stopwords import (
    DEFAULT_RETRIEVAL_STOPWORDS_PATH,
)


def test_load_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "stopwords.txt"
    path.write_text(
        "# leading comment\n"
        "хотеть\n"
        "\n"
        "# section header\n"
        "поехать\n"
        "   \n"
        "на\n",
        encoding="utf-8",
    )
    entries = load_retrieval_stopwords(path)
    assert entries == frozenset({"хотеть", "поехать", "на"})


def test_load_lowercases_entries(tmp_path: Path) -> None:
    path = tmp_path / "stopwords.txt"
    path.write_text("ХОТЕТЬ\nПоехать\n", encoding="utf-8")
    entries = load_retrieval_stopwords(path)
    assert entries == frozenset({"хотеть", "поехать"})


def test_load_handles_default_path_when_none_passed() -> None:
    entries = load_retrieval_stopwords(None)
    assert "хотеть" in entries
    assert "поехать" in entries
    assert "на" in entries
    assert "багги" not in entries
    assert "тур" not in entries


def test_default_path_resolves_to_data_directory() -> None:
    assert DEFAULT_RETRIEVAL_STOPWORDS_PATH.name == "russian_retrieval_stopwords.txt"
    assert DEFAULT_RETRIEVAL_STOPWORDS_PATH.parent.name == "data"
    assert DEFAULT_RETRIEVAL_STOPWORDS_PATH.exists()


def test_get_retrieval_stopwords_returns_same_instance() -> None:
    first = get_retrieval_stopwords()
    second = get_retrieval_stopwords()
    assert first is second
    assert "хотеть" in first
