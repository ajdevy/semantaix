"""Operator KB-upload intent detection (Russian-first, local, free).

Two entry points:

- `/kb_add [confidential]` slash command (case-insensitive on the flag).
- Russian free-text phrases declared in `data/russian_kb_intent_phrases.txt`.

The lemma fallback uses an ordered-subsequence match against the phrase's
lemmas so inflected variants and small filler words (e.g.
"добавь это в базе знаний") still match the canonical "добавь в базу знаний".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

_SLASH_RE = re.compile(r"^\s*/kb_add(?:\s+(confidential))?\s*$", re.IGNORECASE)

_CONFIDENTIAL_LITERALS: tuple[str, ...] = (
    "конфиденциально",
    "приватно",
    "секрет",
    "не для цитирования",
)
_CONFIDENTIAL_LEMMAS: tuple[tuple[str, ...], ...] = (
    ("конфиденциально",),
    ("приватный",),
    ("секрет",),
    ("не", "для", "цитирование"),
)


class _Normalizer(Protocol):
    def lemmas(self, text: str) -> list[str]: ...


@dataclass(frozen=True)
class KbIntent:
    confidential: bool
    mode: Literal["slash", "freetext"]
    cleaned_text: str


@lru_cache(maxsize=1)
def _load_phrases(path: str) -> tuple[str, ...]:
    raw = Path(path).read_text(encoding="utf-8")
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def _default_phrases_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "data" / "russian_kb_intent_phrases.txt")


def _is_ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return False
    i = 0
    for token in haystack:
        if token == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False


def _detect_confidential(*, lowered: str, lemmas: list[str]) -> bool:
    for literal in _CONFIDENTIAL_LITERALS:
        if literal in lowered:
            return True
    for lemma_seq in _CONFIDENTIAL_LEMMAS:
        if _is_ordered_subsequence(list(lemma_seq), lemmas):
            return True
    return False


def _strip_literal_trigger(text: str, trigger: str) -> str:
    pattern = re.compile(re.escape(trigger), re.IGNORECASE)
    return pattern.sub("", text, count=1).strip()


def detect_kb_intent(
    *,
    text: str,
    caption: str | None,
    normalizer: _Normalizer,
    phrases_path: str | None = None,
) -> KbIntent | None:
    """Detect whether the operator wants to push content into the KB.

    Args:
      text: message body (may be empty when the operator attached files only).
      caption: optional caption attached to media; checked first when present.
      normalizer: provides `.lemmas(text) -> list[str]` (typically a
        `RussianNormalizer`).
      phrases_path: override the seed-phrases file (defaults to repo data dir).

    Returns:
      KbIntent describing slash vs free-text mode, confidentiality flag, and
      `cleaned_text` (the input minus the trigger), or None if no intent.
    """
    candidates: list[str] = [s for s in (caption, text) if s and s.strip()]
    if not candidates:
        return None

    for candidate in candidates:
        slash_match = _SLASH_RE.match(candidate.strip())
        if slash_match:
            confidential = slash_match.group(1) is not None
            return KbIntent(confidential=confidential, mode="slash", cleaned_text="")

    phrases_file = phrases_path or _default_phrases_path()
    phrases = _load_phrases(phrases_file)

    for candidate in candidates:
        lowered = candidate.lower()
        for phrase in phrases:
            if phrase in lowered:
                cleaned = _strip_literal_trigger(candidate, phrase)
                confidential = _detect_confidential(
                    lowered=lowered,
                    lemmas=normalizer.lemmas(candidate),
                )
                return KbIntent(
                    confidential=confidential,
                    mode="freetext",
                    cleaned_text=cleaned,
                )

    for candidate in candidates:
        lemmas = normalizer.lemmas(candidate)
        for phrase in phrases:
            phrase_lemmas = normalizer.lemmas(phrase)
            if _is_ordered_subsequence(phrase_lemmas, lemmas):
                confidential = _detect_confidential(
                    lowered=candidate.lower(),
                    lemmas=lemmas,
                )
                return KbIntent(
                    confidential=confidential,
                    mode="freetext",
                    cleaned_text=candidate.strip(),
                )

    return None
