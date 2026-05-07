from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app.answer_trace import AnswerTraceRepository
from services.web_ui.app import main as web_ui_main
from services.web_ui.app.main import app as web_ui_app


def _build_repo(tmp_path) -> AnswerTraceRepository:
    return AnswerTraceRepository(
        db_path=str(tmp_path / "answer_traces.sqlite3"),
        snippet_max_chars=80,
    )


def _seed_trace(repo: AnswerTraceRepository, trace_id: str) -> None:
    repo.write(
        trace_id=trace_id,
        request_text="reset password help",
        model_id="model-x",
        model_provider="openrouter",
        latency_ms=42,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=["ok"],
        guardrail_score=0.91,
        retrieval=[
            {
                "chunk_id": "1",
                "source_ref": "kb",
                "score": 0.5,
                "text_snippet": "reset link via email",
            }
        ],
        confidence=0.91,
        limitations=[],
    )


def test_list_view_shows_empty_state(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    monkeypatch.setattr(web_ui_main, "answer_trace_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/answer-traces")

    assert response.status_code == 200
    assert "No answer traces persisted yet." in response.text


def test_list_view_renders_recent_traces(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    _seed_trace(repo, "t-A")
    _seed_trace(repo, "t-B")
    monkeypatch.setattr(web_ui_main, "answer_trace_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/answer-traces", params={"limit": 10})

    assert response.status_code == 200
    assert "t-A" in response.text and "t-B" in response.text
    assert response.text.index("t-B") < response.text.index("t-A")


def test_detail_view_renders_all_sections(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    _seed_trace(repo, "trace-detail")
    monkeypatch.setattr(web_ui_main, "answer_trace_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/answer-traces/trace-detail")

    assert response.status_code == 200
    text = response.text
    assert "<h2>Sources</h2>" in text
    assert "kb" in text and "0.500" in text
    assert "<h2>Policy / guardrails</h2>" in text
    assert "valid" in text and "ok" in text
    assert "<h2>Model routing</h2>" in text
    assert "model-x" in text and "openrouter" in text and "42 ms" in text
    assert "<h2>Confidence / limitations</h2>" in text
    assert "Grounded" in text


def test_detail_view_shows_no_retrieval_message_when_empty(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    repo.write(
        trace_id="trace-empty",
        request_text="?",
        model_id=None,
        model_provider=None,
        latency_ms=None,
        response_mode="suggestion_only",
        guardrails_applied=True,
        guardrail_outcome="valid",
        guardrail_reasons=[],
        guardrail_score=None,
        retrieval=[],
        confidence=None,
        limitations=["partial_context"],
    )
    monkeypatch.setattr(web_ui_main, "answer_trace_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/answer-traces/trace-empty")

    assert response.status_code == 200
    text = response.text
    assert "No retrieval hit." in text
    assert "partial_context" in text


def test_detail_view_handles_missing_trace(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    monkeypatch.setattr(web_ui_main, "answer_trace_repository", repo)
    client = TestClient(web_ui_app)

    response = client.get("/answer-traces/missing-id")

    assert response.status_code == 200
    assert "Answer trace not found" in response.text
    assert "answer_trace_persistence_failures" in response.text
