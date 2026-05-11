from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pymorphy3
from razdel import tokenize

from services.api.app.russian_text.profanity import load_profanity
from services.api.app.russian_text.slang import load_slang

_PUNCT_STRIP = ".,!?;:()[]{}\"'«»—–…„""``"


class RussianNormalizer:
    def __init__(
        self,
        *,
        slang_path: str | Path | None = None,
        profanity_path: str | Path | None = None,
    ) -> None:
        self._slang = load_slang(str(slang_path) if slang_path else None)
        self._profanity = load_profanity(str(profanity_path) if profanity_path else None)
        self._morph = pymorphy3.MorphAnalyzer()

    def normalize(self, text: str) -> str:
        """Lowercase + whole-token slang substitution. Punctuation preserved."""
        lowered = text.lower()
        parts: list[str] = []
        for substring in tokenize(lowered):
            token = substring.text
            replacement = self._slang.get(token, token)
            parts.append(replacement)
        return " ".join(parts)

    def lemmas(self, text: str) -> list[str]:
        """Tokenize + slang substitute + lemmatize. Returns lemma tokens.

        Pure-punctuation tokens are dropped. Slang substitutions may expand
        to multi-word phrases ("мб" -> "может быть"); those get split and
        lemmatized individually.
        """
        lowered = text.lower()
        result: list[str] = []
        for substring in tokenize(lowered):
            token = substring.text
            replacement = self._slang.get(token, token)
            for piece in replacement.split():
                cleaned = piece.strip(_PUNCT_STRIP)
                if not cleaned:
                    continue
                if not any(ch.isalnum() for ch in cleaned):
                    continue
                parsed = self._morph.parse(cleaned)
                lemma = parsed[0].normal_form if parsed else cleaned
                result.append(lemma)
        return result

    def contains_profanity(self, text: str) -> bool:
        """True if any lemma in `text` matches a profanity entry."""
        return bool(set(self.lemmas(text)) & self._profanity)


@lru_cache(maxsize=1)
def get_russian_normalizer() -> RussianNormalizer:
    return RussianNormalizer()
