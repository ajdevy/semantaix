from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from services.api.app.russian_text import get_russian_normalizer

DEFAULT_HEDGES_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "russian_hedges.txt"
)
DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "russian_policy_phrases.txt"
)


@dataclass(frozen=True)
class GuardrailDecision:
    valid: bool
    reasons: list[str]
    score: float


@lru_cache(maxsize=4)
def _load_phrase_list(path: str) -> tuple[str, ...]:
    """Load phrase entries from a file, drop comments/blanks, and normalize
    so substring matches align with the candidate text after normalization.
    """
    resolved = Path(path)
    raw: list[str] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        raw.append(stripped)
    return _normalize_phrases(raw)


def _normalize_phrases(phrases: Sequence[str]) -> tuple[str, ...]:
    normalizer = get_russian_normalizer()
    return tuple(
        normalizer.normalize(p) for p in phrases if p and p.strip()
    )


def evaluate_suggestion(
    candidate: str,
    *,
    hedge_phrases: Sequence[str] | None = None,
    policy_phrases: Sequence[str] | None = None,
) -> GuardrailDecision:
    """Score a candidate answer against guardrail lists.

    ``hedge_phrases`` and ``policy_phrases`` accept project-scoped overrides
    (raw phrases, one per item; normalization is applied inside). Passing
    ``None`` (the default) reads the canonical files at
    ``data/russian_hedges.txt`` / ``data/russian_policy_phrases.txt``.
    """
    reasons: list[str] = []
    text = candidate.strip()

    if not text:
        reasons.append("empty_response")
    if len(text) > 2000:
        reasons.append("too_long")
    if len(text.split()) < 3:
        reasons.append("insufficient_content")

    normalized = get_russian_normalizer().normalize(text) if text else ""
    resolved_policy = (
        _normalize_phrases(policy_phrases)
        if policy_phrases is not None
        else _load_phrase_list(str(DEFAULT_POLICY_PATH))
    )
    resolved_hedges = (
        _normalize_phrases(hedge_phrases)
        if hedge_phrases is not None
        else _load_phrase_list(str(DEFAULT_HEDGES_PATH))
    )

    if any(phrase and phrase in normalized for phrase in resolved_policy):
        reasons.append("policy_violation")
    if any(phrase and phrase in normalized for phrase in resolved_hedges):
        reasons.append("low_confidence")

    if reasons:
        return GuardrailDecision(valid=False, reasons=reasons, score=0.2)
    return GuardrailDecision(valid=True, reasons=[], score=0.95)
