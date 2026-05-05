from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.web_ui.app.main import app as web_app


def test_api_bootstrap_health_live():
    client = TestClient(api_app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["service"] == "api"


def test_web_ui_shell_smoke():
    client = TestClient(web_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Semantaix Admin" in response.text
