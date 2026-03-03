import os
import sys
import pytest

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

@pytest.fixture(scope="session")
def base_url() -> str:
    url = (os.getenv("BASE_URL") or os.getenv("APP_BASE_URL") or "").strip()
    return (url or "https://example.com").rstrip("/")
