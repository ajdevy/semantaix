from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailDecision:
    valid: bool
    reasons: list[str]
    score: float


def evaluate_suggestion(candidate: str) -> GuardrailDecision:
    reasons: list[str] = []
    text = candidate.strip()
    lowered = text.lower()

    if not text:
        reasons.append("empty_response")
    if len(text) > 2000:
        reasons.append("too_long")
    if len(text.split()) < 3:
        reasons.append("insufficient_content")

    blocked_phrases = [
        "ignore previous instructions",
        "bypass policy",
        "credit card number",
    ]
    if any(phrase in lowered for phrase in blocked_phrases):
        reasons.append("policy_violation")
    if "i don't know" in lowered or "i am not sure" in lowered:
        reasons.append("low_confidence")

    if reasons:
        return GuardrailDecision(valid=False, reasons=reasons, score=0.2)
    return GuardrailDecision(valid=True, reasons=[], score=0.95)
