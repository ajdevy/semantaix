from fastapi.testclient import TestClient

from services.api.app.main import app as api_app
from services.ingest_worker.app.main import app as ingest_app
from services.scheduler.app.main import app as scheduler_app
from services.web_ui.app.main import app as web_app


def test_api_bootstrap_health_live():
    client = TestClient(api_app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["service"] == "api"


def test_api_root_smoke():
    client = TestClient(api_app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"service": "api", "message": "Semantaix API"}


def test_web_ui_shell_smoke():
    client = TestClient(web_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Semantaix Admin" in response.text


def test_web_ui_alerts_shell_smoke():
    client = TestClient(web_app)
    response = client.get("/alerts")
    assert response.status_code == 200
    assert "Semantaix Alerts" in response.text


def test_ingest_worker_bootstrap_health_ready():
    client = TestClient(ingest_app)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["service"] == "ingest_worker"


def test_scheduler_bootstrap_health_startup():
    client = TestClient(scheduler_app)
    response = client.get("/health/startup")
    assert response.status_code == 200
    assert response.json()["service"] == "scheduler"
