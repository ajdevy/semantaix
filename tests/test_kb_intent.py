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
    assert result == KbIntent(
        confidential=False, mode="slash", cleaned_text="", match_kind="slash"
    )


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


def test_freetext_with_english_knowledge_base(normalizer, phrases_path):
    result = detect_kb_intent(
        text="хочу добавить материалы в knowledge base",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"
    assert result.confidential is False
    # Original English form preserved in cleaned_text so the operator's
    # words are what reach _process_operator_upload.
    assert "knowledge base" in result.cleaned_text.lower()


def test_freetext_with_kb_token(normalizer, phrases_path):
    result = detect_kb_intent(
        text="добавить документы в kb",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"


def test_freetext_infinitive_russian_only(normalizer, phrases_path):
    result = detect_kb_intent(
        text="хочу добавить это в базу знаний",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"


def test_caption_with_english_knowledge_base(normalizer, phrases_path):
    result = detect_kb_intent(
        text="",
        caption="хочу добавить материалы в knowledge base",
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.mode == "freetext"


def test_kb_token_inside_word_does_not_trigger(normalizer, phrases_path):
    # `\bkb\b` must not match "kb" embedded in another token (word-boundary check).
    result = detect_kb_intent(
        text="это про skbarn, не про базу",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is None


def test_weather_question_does_not_trigger(normalizer, phrases_path):
    result = detect_kb_intent(
        text="какая сегодня погода в москве",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is None


def test_normalize_kb_synonyms_helper():
    from services.bot_gateway.app.kb_intent import _normalize_kb_synonyms

    assert _normalize_kb_synonyms("в knowledge base") == "в базу знаний"
    assert _normalize_kb_synonyms("Knowledge-Base") == "базу знаний"
    assert _normalize_kb_synonyms("в kb срочно") == "в базу знаний срочно"
    # word boundaries: "kb" inside another token must NOT be replaced
    assert _normalize_kb_synonyms("skbarn") == "skbarn"


def test_match_kind_slash(normalizer, phrases_path):
    result = detect_kb_intent(
        text="/kb_add",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.match_kind == "slash"


def test_match_kind_literal(normalizer, phrases_path):
    # Phrase appears verbatim as a substring → literal-match branch.
    result = detect_kb_intent(
        text="добавь в базу: офис открыт по будням с 9 до 18",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.match_kind == "literal"


def test_match_kind_lemma(normalizer, phrases_path):
    # Inflected form with filler word → only the lemma fallback catches it.
    result = detect_kb_intent(
        text="добавьте это в базе знаний пожалуйста",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.match_kind == "lemma"


def test_match_kind_english_normalization_is_lemma(normalizer, phrases_path):
    # The operator's natural phrasing — meta-request, not real KB content.
    # Should land in lemma branch so callers can refuse to ingest it as inline_text.
    result = detect_kb_intent(
        text="хочу добавить материалы в knowledge base",
        caption=None,
        normalizer=normalizer,
        phrases_path=phrases_path,
    )
    assert result is not None
    assert result.match_kind == "lemma"
