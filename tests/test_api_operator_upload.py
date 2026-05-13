from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.main import (
    app as api_app,
)
from services.api.app.main import (
    incident_repository,
    knowledge_moderation_repository,
    rag_repository,
)
from services.api.app.operator_uploads import extractors


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    monkeypatch.setattr(api_main, "_operator_transcriber", None, raising=False)
    return tmp_path


def _make_file(path: Path, contents: bytes = b"binary") -> Path:
    path.write_bytes(contents)
    return path


def test_inline_text_creates_approved_candidate(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@ajdevy",
            "source_file_type": "inline_text",
            "inline_text": "Время работы офиса: будни с 9 до 18.",
            "is_confidential": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deduplicated"] is False
    assert body["inserted_chunks"] >= 1
    candidate_id = body["candidate_id"]
    row = knowledge_moderation_repository.get(candidate_id)
    assert row.status == "approved"
    assert row.source_file_type == "inline_text"
    assert row.uploaded_by_operator_username == "@ajdevy"
    assert row.binary_sha256 is None


def test_unsupported_file_type_returns_422(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "bogus",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported_source_file_type"


def test_inline_text_empty_returns_422(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "inline_text",
            "inline_text": "   ",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "empty_inline_text"


def test_missing_stored_binary_path_returns_422(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "pdf",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "missing_stored_binary_path"


def test_missing_binary_file_returns_404(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "pdf",
            "stored_binary_path": "/no/such/path.pdf",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "binary_not_found"


def test_text_file_extraction_happy_path(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "doc.txt", "Краткое описание услуги.".encode("utf-8"))
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "source_file_name": "doc.txt",
            "stored_binary_path": str(txt),
            "is_confidential": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["inserted_chunks"] >= 1
    candidate = knowledge_moderation_repository.get(body["candidate_id"])
    assert candidate.binary_sha256 is not None
    assert candidate.source_file_type == "txt"


def test_dedup_short_circuits_on_repeated_upload(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "dup.txt", "Содержимое для дедупликации.".encode("utf-8"))

    spy_calls: list[Path] = []
    real_extract = extractors.EXTRACTORS["txt"]

    def tracking_extract(path):
        spy_calls.append(path)
        return real_extract(path)

    monkeypatch.setitem(extractors.EXTRACTORS, "txt", tracking_extract)
    client = TestClient(api_app)
    first = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert first.status_code == 200
    assert first.json()["deduplicated"] is False

    second = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert second.status_code == 200
    body = second.json()
    assert body["deduplicated"] is True
    assert body["inserted_chunks"] == 0
    assert len(spy_calls) == 1  # extractor invoked only on the first upload


def test_confidential_flag_propagates_to_rag_chunks(isolated_paths):
    txt = _make_file(
        isolated_paths / "secret.txt",
        "Конфиденциальные расценки внутреннего отдела.".encode("utf-8"),
    )
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
            "is_confidential": True,
        },
    )
    assert response.status_code == 200
    candidate_id = response.json()["candidate_id"]
    candidate = knowledge_moderation_repository.get(candidate_id)
    assert candidate.is_confidential is True

    chunks = rag_repository.retrieve(query="конфиденциальные расценки", limit=5)
    assert chunks
    for chunk in chunks:
        if chunk.source_id == f"knowledge_candidate:{candidate_id}":
            assert chunk.is_confidential is True
            break
    else:
        pytest.fail("inserted confidential chunk not found in retrieval")


def test_empty_extraction_returns_422(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "blank.txt", "   ".encode("utf-8"))
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "empty_text"


def test_extractor_raises_unexpected_exception_creates_incident(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "boom.txt", b"data")

    def boom(_path):
        raise RuntimeError("extractor disk burned down")

    monkeypatch.setitem(extractors.EXTRACTORS, "txt", boom)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "operator_upload_failed"
    incidents = client.get("/incidents/operator_upload_failures").json()["items"]
    assert len(incidents) == 1


def test_audio_extraction_uses_transcriber(isolated_paths, monkeypatch):
    audio = _make_file(isolated_paths / "voice.ogg", b"opus-bytes")

    class FakeTranscriber:
        def transcribe(self, path, *, language):
            return "Расшифровка голосового сообщения"

    async def fake_extract_media(source_file_type, path, *, transcriber, max_seconds=None):
        return transcriber.transcribe(path, language="ru")

    monkeypatch.setattr(api_main, "_operator_transcriber", FakeTranscriber(), raising=False)
    monkeypatch.setattr(
        extractors,
        "extract_media",
        fake_extract_media,
    )
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "audio",
            "stored_binary_path": str(audio),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["inserted_chunks"] >= 1


def test_video_extraction_uses_transcriber(isolated_paths, monkeypatch):
    video = _make_file(isolated_paths / "v.mp4", b"video-bytes")

    class FakeTranscriber:
        def transcribe(self, path, *, language):
            return "видеозапись содержит инструкцию"

    async def fake_extract_media(source_file_type, path, *, transcriber, max_seconds=None):
        assert source_file_type == "video"
        return transcriber.transcribe(path, language="ru")

    monkeypatch.setattr(api_main, "_operator_transcriber", FakeTranscriber(), raising=False)
    monkeypatch.setattr(extractors, "extract_media", fake_extract_media)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "video",
            "stored_binary_path": str(video),
        },
    )
    assert response.status_code == 200


def test_extraction_raises_extraction_error(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "x.txt", b"hi")

    def raise_extraction(_path):
        raise extractors.ExtractionError("bad_format")

    monkeypatch.setitem(extractors.EXTRACTORS, "txt", raise_extraction)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "bad_format"


def test_transcriber_singleton_is_lazy_instantiated(isolated_paths, monkeypatch):
    api_main._operator_transcriber = None

    class FakeWhisper:
        def __init__(self):
            self.created = True

        def transcribe(self, path, *, language):
            return "audio extract"

    monkeypatch.setattr(
        "services.api.app.operator_uploads.extractors.WhisperTranscriber",
        FakeWhisper,
    )
    first = api_main._get_operator_transcriber()
    second = api_main._get_operator_transcriber()
    assert first is second
    assert isinstance(first, FakeWhisper)


def test_extractor_raising_http_exception_propagates(isolated_paths, monkeypatch):
    from fastapi import HTTPException

    txt = _make_file(isolated_paths / "x.txt", b"hi")

    def raise_http(_path):
        raise HTTPException(status_code=418, detail="teapot")

    monkeypatch.setitem(extractors.EXTRACTORS, "txt", raise_http)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert response.status_code == 418
    assert response.json()["detail"] == "teapot"


def test_soft_wrap_collapsing_to_empty_returns_422(isolated_paths, monkeypatch):
    txt = _make_file(isolated_paths / "ok.txt", "Содержимое".encode("utf-8"))
    monkeypatch.setattr(extractors, "soft_wrap", lambda text, max_chars=200: "")
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload",
        json={
            "operator_username": "@op",
            "source_file_type": "txt",
            "stored_binary_path": str(txt),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "empty_text"
