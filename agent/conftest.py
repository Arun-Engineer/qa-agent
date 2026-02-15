import os
import pytest

@pytest.fixture(scope="session")
def base_url() -> str:
    # pytest-playwright expects this fixture name
    url = (os.getenv("BASE_URL") or os.getenv("APP_BASE_URL") or "").strip()
    if not url:
        url = "https://example.com"
    return url.rstrip("/")
