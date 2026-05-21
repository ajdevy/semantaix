"""Detect a customer "what else do you offer?" catalog request (Russian-first).

A broad catalog question ("какие ещё услуги есть", "что можете предложить",
"что ещё есть") rarely shares content words with the knowledge-base chunks that
describe individual items/services, so plain lemma-overlap retrieval misses them.
When this intent fires, `GroundedRagAnswerer` switches to catalog retrieval so the
grounded LLM can synthesise the list from whatever the project's KB holds.

Matching mirrors `services.bot_gateway.app.kb_intent`: a literal substring pass
first, then an ordered-subsequence lemma fallback so inflected variants and small
filler words still match the canonical phrase. Phrases live in
`data/russian_service_catalog_phrases.txt`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol


class _Normalizer(Protocol):
    def lemmas(self, text: str) -> list[str]: ...


@lru_cache(maxsize=8)
def _load_phrases(path: str) -> tuple[str, ...]:
    raw = Path(path).read_text(encoding="utf-8")
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def _default_phrases_path() -> str:
    return str(
        Path(__file__).resolve().parents[4]
        / "data"
        / "russian_service_catalog_phrases.txt"
    )


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


def is_service_catalog_query(
    *,
    text: str,
    normalizer: _Normalizer,
    phrases_path: str | None = None,
) -> bool:
    """Return True when the customer is asking for the catalog of offerings.

    Args:
      text: the customer message body.
      normalizer: provides `.lemmas(text) -> list[str]` (typically a
        `RussianNormalizer`).
      phrases_path: override the seed-phrases file (defaults to repo data dir).
    """
    if not text or not text.strip():
        return False

    phrases = _load_phrases(phrases_path or _default_phrases_path())
    lowered = text.lower()
    for phrase in phrases:
        if phrase in lowered:
            return True

    lemmas = normalizer.lemmas(text)
    for phrase in phrases:
        if _is_ordered_subsequence(normalizer.lemmas(phrase), lemmas):
            return True
    return False
