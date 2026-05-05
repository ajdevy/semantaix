from fastapi.testclient import TestClient

from services.api.app.main import app as api_app


def test_suggest_returns_503_without_openrouter_key():
    client = TestClient(api_app)
    response = client.post("/suggest", json={"text": "Hello"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["detail"]
