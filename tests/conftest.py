"""Shared test fixtures."""
import pytest
from fastapi.testclient import TestClient
from src.api.main import create_app
from src.api.dependencies import reset_stores


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all stores between tests."""
    reset_stores()
    yield
    reset_stores()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    """Test client with API key pre-set."""
    c = TestClient(app)
    c.headers["X-API-Key"] = "dev-secret-key-12345"
    return c


@pytest.fixture
def unauth_client(app):
    """Test client WITHOUT API key."""
    return TestClient(app)
