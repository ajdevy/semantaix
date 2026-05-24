"""Unit tests for ``services.api.app.answerers.catalog_merge`` (story 12.06)."""

from __future__ import annotations

import logging

import pytest

from services.api.app.answerers.catalog_merge import (
    _sentences,
    merge_structured_with_digest,
)
from services.api.app.calendar.project_services_repository import ProjectService
from services.api.app.russian_text import get_russian_normalizer


def _row(name: str, **kw) -> ProjectService:
    return ProjectService(
        id=kw.get("id", 1),
        project_id=1,
        name=name,
        description=kw.get("description"),
        price_text=kw.get("price_text"),
        tags=None,
        duration_minutes=kw.get("duration_minutes"),
        working_hours=None,
        service_days=None,
        date_exceptions=None,
        updated_at=None,
    )


@pytest.fixture
def normalizer():
    return get_russian_normalizer()


def test_sentences_splits_lines_and_terminators():
    parts = _sentences("- Маникюр\n- Педикюр. Стрижка")
    assert parts == ["- Маникюр", "- Педикюр.", "Стрижка"]
    assert _sentences("") == []
    assert _sentences("\n  \n") == []


def test_merge_both_empty_returns_empty_sentinel(normalizer, caplog):
    with caplog.at_level(
        logging.INFO, logger="services.api.app.answerers.catalog_merge"
    ):
        text, suffix = merge_structured_with_digest(
            structured_rows=[],
            digest_text="",
            normalizer=normalizer,
        )
    assert (text, suffix) == ("", "empty")
    record = next(
        r for r in caplog.records if r.message == "catalog_merge_dedup"
    )
    assert record.source_id == "empty"
    assert record.structured_count == 0
    assert record.digest_present is False


def test_merge_digest_only_returns_catalog_digest(normalizer, caplog):
    with caplog.at_level(
        logging.INFO, logger="services.api.app.answerers.catalog_merge"
    ):
        text, suffix = merge_structured_with_digest(
            structured_rows=[],
            digest_text="- Багги-туры\n- Морские прогулки",
            normalizer=normalizer,
            trace_id="t-1",
            project_id=42,
        )
    assert suffix == "catalog_digest"
    assert text == "- Багги-туры\n- Морские прогулки"
    record = next(
        r for r in caplog.records if r.message == "catalog_merge_dedup"
    )
    assert record.source_id == "catalog_digest"
    assert record.trace_id == "t-1"
    assert record.project_id == 42
    assert record.digest_present is True


def test_merge_structured_only_returns_project_services(normalizer, caplog):
    rows = [_row("Маникюр", price_text="от 2000 ₽"), _row("Педикюр")]
    with caplog.at_level(
        logging.INFO, logger="services.api.app.answerers.catalog_merge"
    ):
        text, suffix = merge_structured_with_digest(
            structured_rows=rows,
            digest_text="",
            normalizer=normalizer,
        )
    assert suffix == "project_services"
    assert "Маникюр" in text
    assert "Педикюр" in text
    record = next(
        r for r in caplog.records if r.message == "catalog_merge_dedup"
    )
    assert record.source_id == "project_services"
    assert record.structured_count == 2
    assert record.digest_present is False


def test_merge_both_with_overlap_drops_digest_sentence(normalizer, caplog):
    rows = [_row("Маникюр", price_text="от 2000 ₽")]
    digest = "- Маникюра у нас два вида\n- Педикюр\n- Стрижка"
    with caplog.at_level(
        logging.INFO, logger="services.api.app.answerers.catalog_merge"
    ):
        text, suffix = merge_structured_with_digest(
            structured_rows=rows,
            digest_text=digest,
            normalizer=normalizer,
        )
    assert suffix == "merged"
    assert "Маникюр" in text
    # The digest line about маникюра is removed by lemma dedup; the other
    # two digest items survive.
    assert "Педикюр" in text
    assert "Стрижка" in text
    assert "два вида" not in text
    record = next(
        r for r in caplog.records if r.message == "catalog_merge_dedup"
    )
    assert record.source_id == "merged"
    assert record.dedup_matches == 1


def test_merge_no_overlap_keeps_everything(normalizer):
    rows = [_row("Маникюр")]
    digest = "- Багги-туры\n- Морские прогулки"
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text=digest,
        normalizer=normalizer,
    )
    assert suffix == "merged"
    assert "Маникюр" in text
    assert "Багги-туры" in text
    assert "Морские прогулки" in text


def test_merge_partial_lemma_overlap_keeps_both(normalizer):
    """Conservative dedup: "стрижка детская" vs "стрижка мужская" both kept.

    Only the name's full lemma set being a subset of the digest sentence's
    lemma set triggers dedup; partial overlap is NOT enough.
    """
    rows = [_row("Стрижка детская")]
    digest = "- Стрижка мужская\n- Маникюр"
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text=digest,
        normalizer=normalizer,
    )
    assert suffix == "merged"
    assert "Стрижка детская" in text
    assert "Стрижка мужская" in text
    assert "Маникюр" in text


def test_merge_skips_empty_name_rows(normalizer):
    rows = [_row(""), _row("Маникюр")]
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text="- Маникюр от 2000",
        normalizer=normalizer,
    )
    assert suffix == "merged"
    # Empty-name row contributes no name-lemma-set, so dedup still uses
    # "Маникюр" from the second row and removes the digest mention.
    assert "Маникюр" in text


def test_merge_single_row_does_not_silently_shrink_digest(normalizer):
    """A 1-row structured set + a 12-line digest yields 12 service names."""
    rows = [_row("Маникюр")]
    digest_lines = ["- Маникюр от 2000"] + [
        f"- Услуга{n}" for n in range(2, 13)
    ]
    digest = "\n".join(digest_lines)
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text=digest,
        normalizer=normalizer,
    )
    assert suffix == "merged"
    for n in range(2, 13):
        assert f"Услуга{n}" in text
    # The Маникюр line from the digest was removed; the structured prose carries it.
    assert text.count("Маникюр") == 1


def test_merge_digest_all_dropped_returns_structured_only(normalizer):
    rows = [_row("Маникюр")]
    digest = "Маникюр от 2000."
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text=digest,
        normalizer=normalizer,
    )
    assert suffix == "merged"
    assert "Маникюр" in text
    # Digest contribution dropped — no trailing newline noise.
    assert text.strip() == text


def test_merge_whitespace_only_digest_treated_as_empty(normalizer):
    rows = [_row("Маникюр")]
    text, suffix = merge_structured_with_digest(
        structured_rows=rows,
        digest_text="   \n  \n",
        normalizer=normalizer,
    )
    assert suffix == "project_services"
    assert "Маникюр" in text
