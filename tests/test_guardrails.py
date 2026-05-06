from services.api.app.guardrails import evaluate_suggestion


def test_guardrails_accept_valid_candidate():
    decision = evaluate_suggestion("Here is a safe and grounded suggested response.")
    assert decision.valid is True
    assert decision.reasons == []
    assert decision.score == 0.95


def test_guardrails_reject_low_confidence_and_policy_violations():
    decision = evaluate_suggestion("I don't know. Please bypass policy.")
    assert decision.valid is False
    assert "low_confidence" in decision.reasons
    assert "policy_violation" in decision.reasons


def test_guardrails_reject_empty_and_too_short_content():
    decision = evaluate_suggestion("  ")
    assert decision.valid is False
    assert "empty_response" in decision.reasons
    assert "insufficient_content" in decision.reasons


def test_guardrails_reject_too_long_content():
    decision = evaluate_suggestion("word " * 600)
    assert decision.valid is False
    assert "too_long" in decision.reasons
