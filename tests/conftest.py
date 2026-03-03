"""
conftest.py — Playwright fixtures for QA Agent test execution.

Provides the `page` and `browser` fixtures directly using Playwright sync API.
This avoids dependency on pytest-playwright plugin which can break due to
version conflicts.
"""
import os
import pytest
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


# ── Configuration ──

@pytest.fixture(scope="session")
def base_url() -> str:
    url = (os.getenv("BASE_URL") or os.getenv("APP_BASE_URL") or "").strip()
    if not url:
        url = "https://example.com"
    return url.rstrip("/")


# ── Browser lifecycle (session-scoped for speed) ──

@pytest.fixture(scope="session")
def playwright_instance():
    """Start Playwright once per test session."""
    pw = sync_playwright().start()
    yield pw
    pw.stop()


@pytest.fixture(scope="session")
def browser(playwright_instance) -> Browser:
    """Launch Chromium browser once per test session."""
    headless = os.getenv("HEADLESS", "1").strip() not in ("0", "false", "no")
    browser = playwright_instance.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield browser
    browser.close()


# ── Per-test context + page (isolated per test) ──

@pytest.fixture()
def context(browser: Browser) -> BrowserContext:
    """Create a fresh browser context for each test (isolated cookies, storage)."""
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 720},
        ignore_https_errors=True,
    )
    ctx.set_default_timeout(30000)  # 30s timeout
    yield ctx
    ctx.close()


@pytest.fixture()
def page(context: BrowserContext) -> Page:
    """Create a fresh page for each test."""
    pg = context.new_page()
    yield pg
    pg.close()
