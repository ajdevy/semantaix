from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.api.app.main import incident_repository, rag_repository


def test_rag_ingest_and_retrieve_contract(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    client = TestClient(api_app)

    ingest = client.post(
        "/rag/ingest",
        json={"source_id": "faq-password", "text": "Use reset password link\nOpen settings page"},
    )
    assert ingest.status_code == 200
    assert ingest.json()["inserted_chunks"] == 2

    retrieve = client.post("/rag/retrieve", json={"query": "reset password", "limit": 2})
    assert retrieve.status_code == 200
    items = retrieve.json()["items"]
    assert len(items) >= 1
    assert items[0]["source_id"] == "faq-password"


def test_rag_retrieve_empty_query_returns_no_items(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    client = TestClient(api_app)
    retrieve = client.post("/rag/retrieve", json={"query": "   ", "limit": 3})
    assert retrieve.status_code == 200
    assert retrieve.json()["items"] == []


def test_rag_retrieve_with_project_id_filters_results(tmp_path):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    rag_repository.ingest(
        source_id="kb-a", text="Багги-тур проект A", project_id=1
    )
    rag_repository.ingest(
        source_id="kb-b", text="Багги-тур проект B", project_id=2
    )
    rag_repository.ingest(source_id="kb-null", text="Багги-тур без проекта")
    client = TestClient(api_app)
    only_a = client.post(
        "/rag/retrieve",
        json={"query": "багги тур", "limit": 10, "project_id": 1},
    )
    assert only_a.status_code == 200
    source_ids = {item["source_id"] for item in only_a.json()["items"]}
    assert source_ids == {"kb-a", "kb-null"}
    unscoped = client.post(
        "/rag/retrieve", json={"query": "багги тур", "limit": 10}
    )
    assert unscoped.status_code == 200
    assert {item["source_id"] for item in unscoped.json()["items"]} == {
        "kb-a", "kb-b", "kb-null"
    }
    # Response now surfaces project_id and is_confidential per item.
    item = only_a.json()["items"][0]
    assert "project_id" in item
    assert "is_confidential" in item


def test_rag_ingest_failure_emits_incident(tmp_path, monkeypatch):
    rag_repository.db_path = str(tmp_path / "rag.sqlite3")
    incident_repository.db_path = str(tmp_path / "incidents.sqlite3")
    def _raise_ingest(**kwargs):
        raise ValueError("x")

    monkeypatch.setattr(rag_repository, "ingest", _raise_ingest)
    client = TestClient(api_app)
    response = client.post("/rag/ingest", json={"source_id": "s1", "text": "data"})
    assert response.status_code == 500
    assert response.json()["detail"] == "rag_ingest_failed"
    incidents = client.get("/incidents/rag_ingest_failures").json()["items"]
    assert len(incidents) == 1
