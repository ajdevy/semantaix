from __future__ import annotations

from pathlib import Path

import pytest

from services.api.app.russian_text import get_russian_normalizer
from services.bot_gateway.app.kb_intent import KbIntent, detect_kb_intent


@pytest.fixture
def normalizer():
    return get_russian_normalizer()


@pytest.fixture
def phrases_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "data" / "russian_kb_intent_phrases.txt")


def test_slash_command_plain(normalizer, phrases_path):
    result = detect_kb_intent(
        text="/kb_add",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result == KbIntent(confidential=False, mode="slash", cleaned_text="")


def test_slash_command_confidential_flag(normalizer, phrases_path):
    result = detect_kb_intent(
        text="/kb_add confidential",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True
    assert result.mode == "slash"


def test_slash_command_case_insensitive_on_flag(normalizer, phrases_path):
    result = detect_kb_intent(
        text="/KB_ADD CONFIDENTIAL",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True
    assert result.mode == "slash"


def test_slash_command_in_caption(normalizer, phrases_path):
    result = detect_kb_intent(
        text="",
        caption="/kb_add",
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "slash"


def test_literal_free_text_phrase_strips_trigger(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавь в базу: офис открыт по будням с 9 до 18",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"
    assert result.confidential is False
    assert "офис открыт" in result.cleaned_text
    assert "добавь в базу" not in result.cleaned_text.lower()


def test_each_seed_phrase_triggers(normalizer, phrases_path):
    phrases = Path(phrases_path).read_text(encoding="utf-8").splitlines()
    for phrase in [p.strip() for p in phrases if p.strip()]:
        result = detect_kb_intent(
            text=f"{phrase} — расписание работы",
            caption=None,
            normalizer=normalizer,
            phrases_path=phrases_path,
        )
        assert result is not None, f"phrase did not trigger: {phrase}"
        assert result.mode == "freetext"


def test_lemma_fallback_matches_inflected_input(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавьте это в базе знаний пожалуйста",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"


def test_confidential_keyword_promotes_flag(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавь в базу — конфиденциально",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True


def test_confidential_keyword_privatno(normalizer, phrases_path):
    result = detect_kb_intent(
        text="загрузи в kb приватно",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True


def test_negative_does_not_trigger(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавь молока в магазин",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is None


def test_empty_input_returns_none(normalizer, phrases_path):
    assert (
        detect_kb_intent(
            text="",
            caption=None,
            normalizer=normalizer,
            phrases_path=phrases_path,
        )
        is None
    )


def test_caption_preferred_over_text_for_slash(normalizer, phrases_path):
    result = detect_kb_intent(
        text="какой-то текст",
        caption="/kb_add",
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "slash"


def test_lemma_fallback_with_confidential_keyword(normalizer, phrases_path):
    result = detect_kb_intent(
        text="запомните это для базы знаний — секрет",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True


def test_default_phrases_path_loads(normalizer):
    result = detect_kb_intent(
        text="добавь в базу",
        caption=None,
        normalizer=normalizer,
    )
    assert result is not None
    assert result.mode == "freetext"


def test_lemma_fallback_confidential_via_phrase_lemma(normalizer, phrases_path):
    result = detect_kb_intent(
        text="запомните это для базы знаний не для цитирования",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True


def test_confidential_detected_only_via_lemma(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавь в базу — приватная информация",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.confidential is True


def test_is_ordered_subsequence_returns_false_for_empty_needle():
    from services.bot_gateway.app.kb_intent import _is_ordered_subsequence

    assert _is_ordered_subsequence([], ["a", "b"]) is False
