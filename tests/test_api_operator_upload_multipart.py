from __future__ import annotations

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
    settings,
)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    knowledge_moderation_repository.db_path = str(tmp_path / "knowledge.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    storage_dir = tmp_path / "operator_uploads"
    storage_dir.mkdir()
    monkeypatch.setattr(settings, "operator_upload_storage_dir", str(storage_dir))
    monkeypatch.setattr(api_main, "_operator_transcriber", None, raising=False)
    return tmp_path


def test_multipart_inline_text_creates_approved_candidate(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={
            "operator_username": "@ajdevy",
            "is_confidential": "false",
            "inline_text": "График работы: будни с 10 до 19.",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deduplicated"] is False
    assert body["inserted_chunks"] >= 1
    row = knowledge_moderation_repository.get(body["candidate_id"])
    assert row.status == "approved"
    assert row.source_file_type == "inline_text"
    assert row.uploaded_by_operator_username == "@ajdevy"


def test_multipart_file_upload_happy_path_infers_type_from_extension(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
        files={
            "upload": (
                "policy.txt",
                "Возврат товара в течение 14 дней.".encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inserted_chunks"] >= 1
    candidate = knowledge_moderation_repository.get(body["candidate_id"])
    assert candidate.source_file_type == "txt"
    assert candidate.source_file_name == "policy.txt"
    assert candidate.binary_sha256 is not None


def test_multipart_explicit_source_file_type_overrides_extension(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op", "source_file_type": "txt"},
        files={
            "upload": (
                "no-extension",
                "Просто текст.".encode("utf-8"),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 200, response.text
    candidate = knowledge_moderation_repository.get(response.json()["candidate_id"])
    assert candidate.source_file_type == "txt"


def test_multipart_unknown_extension_returns_422(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
        files={
            "upload": (
                "mystery.xyz",
                b"data",
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unknown_source_file_type"


def test_multipart_rejects_both_file_and_inline_text(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={
            "operator_username": "@op",
            "inline_text": "Заметка.",
        },
        files={"upload": ("a.txt", b"some text", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "file_and_inline_text_both_set"


def test_multipart_rejects_when_neither_provided(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "file_or_inline_text_required"


def test_multipart_dedup_short_circuits_on_repeated_upload(isolated_paths):
    client = TestClient(api_app)
    payload = ("dup.txt", "Уникальное содержимое.".encode("utf-8"), "text/plain")
    first = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
        files={"upload": payload},
    )
    assert first.status_code == 200
    assert first.json()["deduplicated"] is False

    second = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
        files={"upload": payload},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["deduplicated"] is True
    assert body["inserted_chunks"] == 0


def test_multipart_confidential_flag_propagates(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op", "is_confidential": "true"},
        files={
            "upload": (
                "secret.txt",
                "Внутренняя политика отдела.".encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200
    candidate = knowledge_moderation_repository.get(response.json()["candidate_id"])
    assert candidate.is_confidential is True


def test_multipart_rejects_when_upload_exceeds_max_bytes(isolated_paths, monkeypatch):
    monkeypatch.setattr(settings, "operator_upload_max_bytes", 8)
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op"},
        files={
            "upload": (
                "big.txt",
                b"0123456789",
                "text/plain",
            )
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "upload_too_large"


def test_infer_source_file_type_returns_none_for_empty_filename():
    from services.api.app.main import _infer_source_file_type

    assert _infer_source_file_type(None) is None
    assert _infer_source_file_type("") is None


@pytest.mark.parametrize(
    "filename,expected_type",
    [
        ("table.xlsx", "xlsx"),
        ("data.csv", "csv"),
        ("page.html", "html"),
        ("page.htm", "html"),
        ("notes.md", "md"),
        ("notes.markdown", "md"),
        ("memo.rtf", "rtf"),
        ("book.epub", "epub"),
        ("bundle.zip", "zip"),
    ],
)
def test_infer_source_file_type_resolves_new_extensions(filename, expected_type):
    from services.api.app.main import _infer_source_file_type

    assert _infer_source_file_type(filename) == expected_type


def test_multipart_inline_text_whitespace_only_treated_as_missing(isolated_paths):
    client = TestClient(api_app)
    response = client.post(
        "/knowledge/operator_upload_multipart",
        data={"operator_username": "@op", "inline_text": "   "},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "file_or_inline_text_required"
