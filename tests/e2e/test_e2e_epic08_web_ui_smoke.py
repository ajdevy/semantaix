"""Epic 08 (partial): static admin shell only until trace APIs ship."""

import pytest
from fastapi.testclient import TestClient

from services.web_ui.app.main import app as web_app

pytestmark = [pytest.mark.e2e, pytest.mark.epic("08"), pytest.mark.story("08-02")]


def test_epic08_admin_shell_reachable():
    client = TestClient(web_app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Semantaix Admin" in response.text


def test_epic08_alerts_shell_reachable():
    client = TestClient(web_app)
    response = client.get("/alerts")
    assert response.status_code == 200
    assert "Semantaix Alerts" in response.text
