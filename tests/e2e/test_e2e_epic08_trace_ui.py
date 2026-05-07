"""Epic 08 Story 02: /suggest persists a trace; web UI renders 'why this answer'."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import (
    answer_trace_repository as api_answer_trace_repository,
)
from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    openrouter_client,
    rag_repository,
)
from services.web_ui.app import main as web_ui_main
from services.web_ui.app.main import app as web_ui_app

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-02")]


def test_epic08_trace_visible_in_web_ui(tmp_path, monkeypatch):
    db_file = str(tmp_path / "answer_traces.sqlite3")
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    api_answer_trace_repository.db_path = db_file
    web_ui_main.answer_trace_repository.db_path = db_file
    rag_repository.ingest(source_id="kb", text="reset password through the email link")
    monkeypatch.setattr(openrouter_client, "suggest", AsyncMock(return_value="Use the reset link."))

    api_client = TestClient(api_app)
    suggest = api_client.post(
        "/suggest",
        json={"text": "reset password help", "trace_id": "epic08-ui"},
    )
    assert suggest.status_code == 200
    assert suggest.json()["trace_id"] == "epic08-ui"

    web_client = TestClient(web_ui_app)
    listing = web_client.get("/answer-traces")
    assert listing.status_code == 200
    assert "epic08-ui" in listing.text

    detail = web_client.get("/answer-traces/epic08-ui")
    assert detail.status_code == 200
    assert "Why this answer" in detail.text
    assert "kb" in detail.text
    assert "openrouter" in detail.text
    assert "valid" in detail.text
