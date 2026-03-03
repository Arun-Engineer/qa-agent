"""Integration tests for Discovery API."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.api.main import create_app
from src.api.dependencies import reset_stores
from src.discovery.site_model import SiteModel, PageInfo, ComponentInfo, ApiEndpoint


@pytest.fixture(autouse=True)
def _reset():
    reset_stores()
    yield
    reset_stores()


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def session_id(client):
    resp = client.post("/api/v1/sessions/", json={
        "user_id": "test-user", "environment": "sit", "task": "Discovery test",
    })
    return resp.json()["session_id"]


def _mock_model():
    return SiteModel(
        base_url="https://example.com",
        pages=[
            PageInfo(url="https://example.com", title="Home", page_type="home",
                     classification_confidence=0.9, status_code=200, depth=0,
                     components=[ComponentInfo(component_type="buttons", selector="#btn", tag="button", text="Click")]),
            PageInfo(url="https://example.com/about", title="About", page_type="about", status_code=200, depth=1),
        ],
        api_endpoints=[ApiEndpoint(method="GET", url="https://example.com/api/data", path="/api/data", status_code=200)],
        total_duration_seconds=5.0,
    )


class TestDiscoveryApi:
    @patch("src.api.routes.discovery.DiscoveryEngine")
    def test_full_flow(self, mock_cls, client, session_id):
        m = _mock_model()
        m.save = MagicMock(return_value="/tmp/test.json")
        mock_cls.return_value.run.return_value = m

        resp = client.post("/api/v1/discovery/", json={"session_id": session_id, "target_url": "https://example.com"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["pages_discovered"] == 2
        run_id = data["run_id"]

        assert client.get(f"/api/v1/discovery/{run_id}").status_code == 200
        assert client.get(f"/api/v1/discovery/{run_id}/pages").json()["total"] == 2
        assert client.get(f"/api/v1/discovery/{run_id}/api-surface").json()["total"] == 1

    def test_bad_session(self, client):
        assert client.post("/api/v1/discovery/", json={"session_id": "nope", "target_url": "https://x.com"}).status_code == 404
