from __future__ import annotations

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
    """Load phrase entries and run each through the same normalization
    that the candidate goes through, so substring matches align even when
    razdel splits contractions / punctuation.
    """
    resolved = Path(path)
    normalizer = get_russian_normalizer()
    entries: list[str] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(normalizer.normalize(stripped))
    return tuple(entries)


def evaluate_suggestion(candidate: str) -> GuardrailDecision:
    reasons: list[str] = []
    text = candidate.strip()

    if not text:
        reasons.append("empty_response")
    if len(text) > 2000:
        reasons.append("too_long")
    if len(text.split()) < 3:
        reasons.append("insufficient_content")

    normalized = get_russian_normalizer().normalize(text) if text else ""
    policy_phrases = _load_phrase_list(str(DEFAULT_POLICY_PATH))
    hedge_phrases = _load_phrase_list(str(DEFAULT_HEDGES_PATH))

    if any(phrase and phrase in normalized for phrase in policy_phrases):
        reasons.append("policy_violation")
    if any(phrase and phrase in normalized for phrase in hedge_phrases):
        reasons.append("low_confidence")

    if reasons:
        return GuardrailDecision(valid=False, reasons=reasons, score=0.2)
    return GuardrailDecision(valid=True, reasons=[], score=0.95)
